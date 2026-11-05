"""도구 효과 해석.

도메인은 도구를 **이미 분류된 효과**로만 다룬다 (ADR-007). 어느 도구가 몇 배인지는
`backend/data/items.json` 에 있고, JSON 로딩은 바깥 계층 몫이다.

도구 효과는 대부분 조건부다 — 타입이 맞아야 하거나(자석), 효과굉장이어야 한다(달인의띠).
그래서 단순한 배율 표가 아니라 기술 타입과 상성을 함께 받아 판정한다.

**전투 상태는 여기서 모른다.** 풍선이 아직 안 터졌는지, 열매를 이미 먹었는지는
한 번의 계산으로 알 수 없다. 호출자가 판단해서 효과를 넘길지 말지 정한다.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Any, Final

from app.domain.exceptions import UnknownItemEffect, UnknownPokemonType
from app.domain.typechart import PokemonType

NEUTRAL: Final[float] = 1.0
SUPER_EFFECTIVE_THRESHOLD: Final[float] = 2.0


class ItemEffectKind(Enum):
    DAMAGE_FLAT = "DAMAGE_FLAT"
    DAMAGE_TYPE = "DAMAGE_TYPE"
    DAMAGE_SUPER_EFFECTIVE = "DAMAGE_SUPER_EFFECTIVE"
    DEFENSIVE_TYPE_RESIST = "DEFENSIVE_TYPE_RESIST"
    TYPE_IMMUNITY = "TYPE_IMMUNITY"
    SPEED = "SPEED"
    KO_GUARD = "KO_GUARD"
    NONE = "NONE"


@dataclass(frozen=True)
class ItemEffect:
    item_id: str
    name_en: str
    kind: ItemEffectKind
    multiplier: float = NEUTRAL
    move_type: PokemonType | None = None
    source: str = "mainline-assumed"


@dataclass(frozen=True)
class ItemCatalog:
    """도구 id -> 효과. JSON 구조를 받아 만든다."""

    _effects: Mapping[str, ItemEffect]

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Mapping[str, Any]]) -> ItemCatalog:
        effects: dict[str, ItemEffect] = {}
        for item_id, entry in raw.items():
            effects[item_id] = ItemEffect(
                item_id=item_id,
                name_en=str(entry.get("name_en", item_id)),
                kind=_parse_kind(item_id, str(entry["kind"])),
                multiplier=float(entry.get("multiplier", NEUTRAL)),
                move_type=_parse_type(entry.get("move_type")),
                source=str(entry.get("source", "mainline-assumed")),
            )
        return cls(_effects=effects)

    def get(self, item_id: str) -> ItemEffect | None:
        return self._effects.get(item_id)


def _parse_kind(item_id: str, raw: str) -> ItemEffectKind:
    try:
        return ItemEffectKind(raw)
    except ValueError as error:
        raise UnknownItemEffect(f"{item_id}: 알 수 없는 효과 종류 {raw!r}") from error


def _parse_type(raw: str | None) -> PokemonType | None:
    if raw is None:
        return None
    try:
        return PokemonType(raw)
    except ValueError as error:
        raise UnknownPokemonType(f"알 수 없는 타입 이름: {raw!r}") from error


def offensive_multiplier(
    effect: ItemEffect | None,
    move_type: PokemonType,
    type_effectiveness: float,
) -> float:
    """공격 측 도구가 데미지에 곱하는 배율."""
    if effect is None:
        return NEUTRAL
    if effect.kind is ItemEffectKind.DAMAGE_FLAT:
        return effect.multiplier
    if effect.kind is ItemEffectKind.DAMAGE_TYPE and effect.move_type is move_type:
        return effect.multiplier
    if (
        effect.kind is ItemEffectKind.DAMAGE_SUPER_EFFECTIVE
        and type_effectiveness >= SUPER_EFFECTIVE_THRESHOLD
    ):
        return effect.multiplier
    return NEUTRAL


def defensive_multiplier(
    effect: ItemEffect | None,
    move_type: PokemonType,
    type_effectiveness: float,
) -> float:
    """방어 측 도구가 데미지에 곱하는 배율 (약점 보호 열매 등)."""
    if effect is None:
        return NEUTRAL
    if (
        effect.kind is ItemEffectKind.DEFENSIVE_TYPE_RESIST
        and effect.move_type is move_type
        and type_effectiveness >= SUPER_EFFECTIVE_THRESHOLD
    ):
        return effect.multiplier
    return NEUTRAL


def grants_immunity(effect: ItemEffect | None, move_type: PokemonType) -> bool:
    """도구가 해당 타입 기술을 무효화하는가.

    무효는 배율이 아니다. 0배를 곱하는 것과 "맞지 않는다"는 다르고,
    최소 데미지 1 규칙이 그 차이에서 갈린다 (CLAUDE.md 5-2).
    """
    if effect is None:
        return False
    return effect.kind is ItemEffectKind.TYPE_IMMUNITY and effect.move_type is move_type
