"""데미지 계산 (Lv.50 고정).

```
기초 = floor(22 × 위력 × 공격실수치 / 방어실수치)
기초 = floor(기초 / 50) + 2
roll_i = floor(기초 × mod × r_i)     r_i ∈ {0.85, 0.86, ..., 1.00}
```

보정 체인은 ADR-003 의 **A안**이다 — 보정을 실수로 누적하고 맨 마지막에 한 번만 내린다.
따라서 **보정끼리의 순서는 결과에 영향이 없다.**

**보정 곱셈은 `Modifiers.apply()` 안에서만 일어난다.** B안(단계마다 내림)으로 바꿔야 할 때
이 메서드 하나만 고치면 되도록 격리했다. 다른 곳에 곱셈이 흩어지면 그 전제가 깨진다.

화상은 보정이 아니다. 공격 실수치를 반감하며 기초 계산 **전**에 적용된다
(champions-helper: "화상 상태에서 물리 기술을 쓰면 공격이 0.5배").
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Final

from app.domain.models import Attacker, Defender, MoveCategory, MoveSpec
from app.domain.typechart import PokemonType, TypeChart, effectiveness

# Lv.50 고정이라 앞 계수가 22로 고정된다: floor(2 × 50 / 5) + 2
LEVEL_COEFFICIENT: Final[int] = 22
BASE_DIVISOR: Final[int] = 50
BASE_OFFSET: Final[int] = 2

STAB_MULTIPLIER: Final[float] = 1.5
CRIT_MULTIPLIER: Final[float] = 1.5
WEATHER_BOOST: Final[float] = 1.5
WEATHER_PENALTY: Final[float] = 0.5
NEUTRAL: Final[float] = 1.0
# 내림 결과가 0이어도 1로 올린다 (본가와 동일, 2026-11-05 확인).
# 단 상성 0배(무효)는 예외로 0을 유지한다.
MINIMUM_DAMAGE: Final[int] = 1

# 난수 0.85 ~ 1.00, 16단계. 평균값 하나로 줄이지 않는다.
ROLL_COUNT: Final[int] = 16
ROLL_MIN_PERCENT: Final[int] = 85
ROLL_SCALE: Final[int] = 100
DAMAGE_ROLLS: Final[tuple[int, ...]] = tuple(ROLL_MIN_PERCENT + step for step in range(ROLL_COUNT))


class Weather(Enum):
    """날씨.

    모래바람·설경은 **데미지 배율이 아니다.** 본가에서는 바위 특방·얼음 방어를 올리는
    스탯 단계 효과이고, 챔피언스의 정확한 규칙은 확인되지 않았다.
    따라서 여기서는 1.0 을 돌려주고, 스탯 보정은 호출자 책임으로 남긴다.
    """

    CLEAR = auto()
    SUN = auto()
    RAIN = auto()
    SAND = auto()
    SNOW = auto()


@dataclass(frozen=True)
class Modifiers:
    """기초 데미지에 곱하는 배율.

    `stab` / `type_effectiveness` / `weather` / `crit` 은 엔진이 계산한다.
    `screen` / `field` / `ability` / `item` 은 **호출자가 결정해 넘긴다** —
    어느 도구가 몇 배인지는 시드 데이터를 읽는 상위 계층이 안다.
    """

    stab: float = NEUTRAL
    type_effectiveness: float = NEUTRAL
    weather: float = NEUTRAL
    crit: float = NEUTRAL
    screen: float = NEUTRAL
    field: float = NEUTRAL
    ability: float = NEUTRAL
    item: float = NEUTRAL

    @property
    def product(self) -> float:
        return (
            self.stab
            * self.type_effectiveness
            * self.weather
            * self.crit
            * self.screen
            * self.field
            * self.ability
            * self.item
        )

    def apply(self, base: int) -> tuple[int, ...]:
        """A안: 보정을 실수로 누적하고 난수를 곱한 뒤 한 번만 내린다.

        ★ 보정 곱셈이 일어나는 유일한 지점이다. B안으로 바꾸려면 여기만 고친다.
        """
        multiplier = self.product
        # 상성 0배(무효)는 0을 유지한다. 맞지 않는 것과 아주 조금 맞는 것은 다르다.
        if self.type_effectiveness == 0:
            return (0,) * ROLL_COUNT
        return tuple(
            max(math.floor(base * multiplier * roll / ROLL_SCALE), MINIMUM_DAMAGE)
            for roll in DAMAGE_ROLLS
        )

    def as_dict(self) -> dict[str, float]:
        """1배인 보정도 전부 노출한다.

        "해당 없음"과 "계산을 빠뜨림"을 구분할 수 있어야 설명이 가능하다.
        """
        return {
            "stab": self.stab,
            "type_effectiveness": self.type_effectiveness,
            "weather": self.weather,
            "crit": self.crit,
            "screen": self.screen,
            "field": self.field,
            "ability": self.ability,
            "item": self.item,
        }


@dataclass(frozen=True)
class DamageResult:
    rolls: tuple[int, ...]
    min_percent: float
    max_percent: float
    ko_summary: str
    ohko_chance: float
    hits_to_ko_min: int
    hits_to_ko_max: int
    applied_modifiers: dict[str, float] = field(default_factory=dict)


def base_damage(*, power: int, attack: int, defense: int) -> int:
    """기초 데미지. 나누기마다 내림한다."""
    if power < 0:
        raise ValueError(f"기술 위력은 음수일 수 없다: {power}")
    if attack <= 0:
        raise ValueError(f"공격 실수치는 1 이상이어야 한다: {attack}")
    if defense <= 0:
        raise ValueError(f"방어 실수치는 1 이상이어야 한다: {defense}")

    raw = LEVEL_COEFFICIENT * power * attack // defense
    return raw // BASE_DIVISOR + BASE_OFFSET


def _weather_multiplier(weather: Weather, move_type: PokemonType) -> float:
    if weather is Weather.SUN:
        if move_type is PokemonType.FIRE:
            return WEATHER_BOOST
        if move_type is PokemonType.WATER:
            return WEATHER_PENALTY
    elif weather is Weather.RAIN:
        if move_type is PokemonType.WATER:
            return WEATHER_BOOST
        if move_type is PokemonType.FIRE:
            return WEATHER_PENALTY
    return NEUTRAL


def _summarise(rolls: tuple[int, ...], hp: int) -> tuple[str, float, int, int]:
    """확정타 판정. 16 roll 중 HP 이상인 개수를 센다."""
    landing = sum(1 for roll in rolls if roll >= hp)
    ohko_chance = landing / ROLL_COUNT

    if rolls[-1] <= 0:
        return "효과 없음", 0.0, 0, 0

    hits_min = math.ceil(hp / rolls[-1])
    hits_max = math.ceil(hp / rolls[0]) if rolls[0] > 0 else 0

    if hits_min == hits_max:
        return f"확정 {hits_min}타", ohko_chance, hits_min, hits_max
    if hits_min == 1:
        percent = ohko_chance * 100
        return f"난수 1타 ({percent:.1f}%)", ohko_chance, hits_min, hits_max
    # n타(n≥2)의 정확한 확률은 roll 합의 분포가 필요하다. 아직 계산하지 않는다.
    return f"난수 {hits_min}타", ohko_chance, hits_min, hits_max


def calculate(
    chart: TypeChart,
    attacker: Attacker,
    defender: Defender,
    move: MoveSpec,
    *,
    weather: Weather = Weather.CLEAR,
    is_critical: bool = False,
    screen: float = NEUTRAL,
    field_multiplier: float = NEUTRAL,
    ability: float = NEUTRAL,
    item: float = NEUTRAL,
) -> DamageResult:
    """16 roll 전부와 확정타 판정을 돌려준다.

    `screen` / `field_multiplier` / `ability` / `item` 은 호출자가 결정한 배율이다.
    도메인은 어느 도구·특성이 몇 배인지 모른다.
    """
    if defender.hp_stat <= 0:
        raise ValueError(f"방어 측 HP 실수치는 1 이상이어야 한다: {defender.hp_stat}")

    modifiers = Modifiers(
        weather=_weather_multiplier(weather, move.move_type),
        crit=CRIT_MULTIPLIER if is_critical else NEUTRAL,
        screen=screen,
        field=field_multiplier,
        ability=ability,
        item=item,
    )

    if move.category is MoveCategory.STATUS:
        return DamageResult(
            rolls=(0,) * ROLL_COUNT,
            min_percent=0.0,
            max_percent=0.0,
            ko_summary="변화 기술",
            ohko_chance=0.0,
            hits_to_ko_min=0,
            hits_to_ko_max=0,
            applied_modifiers=modifiers.as_dict(),
        )

    attack = attacker.offensive_stat(move.category)
    # 화상은 보정이 아니라 스탯 단계다. 기초 계산 전에 내림한다.
    if attacker.is_burned and move.category is MoveCategory.PHYSICAL:
        attack = attack // 2

    modifiers = Modifiers(
        stab=STAB_MULTIPLIER if attacker.has_type(move.move_type) else NEUTRAL,
        type_effectiveness=effectiveness(chart, move.move_type, defender.types),
        weather=modifiers.weather,
        crit=modifiers.crit,
        screen=screen,
        field=field_multiplier,
        ability=ability,
        item=item,
    )

    base = base_damage(
        power=move.power,
        attack=max(attack, 1),
        defense=defender.defensive_stat(move.category),
    )
    rolls = modifiers.apply(base)
    summary, ohko_chance, hits_min, hits_max = _summarise(rolls, defender.hp_stat)

    return DamageResult(
        rolls=rolls,
        min_percent=rolls[0] / defender.hp_stat * 100,
        max_percent=rolls[-1] / defender.hp_stat * 100,
        ko_summary=summary,
        ohko_chance=ohko_chance,
        hits_to_ko_min=hits_min,
        hits_to_ko_max=hits_max,
        applied_modifiers=modifiers.as_dict(),
    )
