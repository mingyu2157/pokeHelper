"""데미지 계산이 필요로 하는 불변 모델.

**이 계층은 '이미 결정된 실수치'를 받는다.** 구애띠·돌격조끼·랭크업처럼 실수치를
바꾸는 효과는 호출자가 반영한 값을 넘긴다. 도메인은 어느 도구가 몇 배인지 모른다.

예외가 하나 있다 — **화상은 여기서 처리한다.** 기초 계산 전에 내림이 한 번 더
끼어들어서 결과가 달라지므로, floor 의미를 엔진이 직접 소유해야 한다.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto

from app.domain.nature import StatKey
from app.domain.typechart import PokemonType


class MoveCategory(Enum):
    PHYSICAL = auto()
    SPECIAL = auto()
    STATUS = auto()


@dataclass(frozen=True)
class MoveSpec:
    """데미지 계산에 필요한 기술 정보만. 명중·PP·부가효과는 여기 없다."""

    power: int
    move_type: PokemonType
    category: MoveCategory


@dataclass(frozen=True)
class Attacker:
    """공격 측. 실수치는 도구·랭크가 이미 반영된 값이다."""

    attack_stat: int
    special_attack_stat: int
    types: tuple[PokemonType, ...]
    is_burned: bool = False

    def offensive_stat(self, category: MoveCategory) -> int:
        if category is MoveCategory.SPECIAL:
            return self.special_attack_stat
        return self.attack_stat

    def has_type(self, move_type: PokemonType) -> bool:
        return move_type in self.types


@dataclass(frozen=True)
class Defender:
    """방어 측. 실수치는 도구·랭크·원더룸이 이미 반영된 값이다."""

    defense_stat: int
    special_defense_stat: int
    hp_stat: int
    types: tuple[PokemonType, ...]

    def defensive_stat(self, category: MoveCategory) -> int:
        if category is MoveCategory.SPECIAL:
            return self.special_defense_stat
        return self.defense_stat


@dataclass(frozen=True)
class BaseStats:
    """포켓몬 고유 종족값 6개. 노력치·성격이 적용되기 전 값이다."""

    hp: int
    attack: int
    defense: int
    sp_attack: int
    sp_defense: int
    speed: int

    def of(self, stat: StatKey) -> int:
        return {
            StatKey.HP: self.hp,
            StatKey.ATTACK: self.attack,
            StatKey.DEFENSE: self.defense,
            StatKey.SP_ATTACK: self.sp_attack,
            StatKey.SP_DEFENSE: self.sp_defense,
            StatKey.SPEED: self.speed,
        }[stat]

    def offensive_key(self, category: MoveCategory) -> StatKey:
        return StatKey.SP_ATTACK if category is MoveCategory.SPECIAL else StatKey.ATTACK

    def defensive_key(self, category: MoveCategory) -> StatKey:
        return StatKey.SP_DEFENSE if category is MoveCategory.SPECIAL else StatKey.DEFENSE
