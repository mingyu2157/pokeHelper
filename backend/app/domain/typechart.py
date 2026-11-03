"""타입 상성.

상성 데이터는 `backend/data/typechart.json` 에 있고 PokéAPI 로 생성한다
(`scripts/generate_typechart.py`). domain 계층은 파일을 읽지 않으므로,
데이터를 읽어 `TypeChart.from_mapping()` 으로 주입하는 것은 바깥 계층의 책임이다.

표에는 **1배(중립)를 담지 않는다.** 324칸 중 204칸이 중립이라 비중립 120칸만 기록하고,
조회 시 없으면 1.0 을 쓴다.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Final

from app.domain.exceptions import UnknownPokemonType

NEUTRAL: Final[float] = 1.0
MAX_DEFENDING_TYPES: Final[int] = 2


class PokemonType(Enum):
    """본가 9세대와 동일한 18타입. 챔피언스 API 전수 조사로 확인했다 (2026-11-03).

    값은 PokéAPI 의 타입 식별자를 그대로 쓴다. 외부 데이터의 표기
    (championsbattledata 는 `Grass` 처럼 대문자로 시작)를 이 식별자로 바꾸는 것은
    ingestion 계층의 책임이다.
    """

    NORMAL = "normal"
    FIGHTING = "fighting"
    FLYING = "flying"
    POISON = "poison"
    GROUND = "ground"
    ROCK = "rock"
    BUG = "bug"
    GHOST = "ghost"
    STEEL = "steel"
    FIRE = "fire"
    WATER = "water"
    GRASS = "grass"
    ELECTRIC = "electric"
    PSYCHIC = "psychic"
    ICE = "ice"
    DRAGON = "dragon"
    DARK = "dark"
    FAIRY = "fairy"


@dataclass(frozen=True)
class TypeChart:
    """공격 타입 -> 방어 타입 -> 배율. 중립 관계는 담지 않는다."""

    _multipliers: Mapping[tuple[PokemonType, PokemonType], float]

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Mapping[str, float]]) -> TypeChart:
        """JSON 구조(문자열 키)를 받아 TypeChart 를 만든다.

        알 수 없는 타입 이름이 있으면 UnknownPokemonType 을 발생시킨다.
        조용히 건너뛰면 그 타입이 걸린 계산이 전부 중립으로 처리돼 틀린 값이 나간다.
        """
        multipliers: dict[tuple[PokemonType, PokemonType], float] = {}
        for attacking_name, row in raw.items():
            attacking = _parse_type(attacking_name)
            for defending_name, multiplier in row.items():
                multipliers[(attacking, _parse_type(defending_name))] = float(multiplier)
        return cls(_multipliers=multipliers)

    def multiplier(self, attacking: PokemonType, defending: PokemonType) -> float:
        """단일 방어 타입에 대한 배율. 표에 없으면 중립이다."""
        return self._multipliers.get((attacking, defending), NEUTRAL)


def _parse_type(name: str) -> PokemonType:
    try:
        return PokemonType(name)
    except ValueError as error:
        raise UnknownPokemonType(f"알 수 없는 타입 이름: {name!r}") from error


def effectiveness(
    chart: TypeChart,
    attacking: PokemonType,
    defending: Sequence[PokemonType],
) -> float:
    """방어 타입 전체에 대한 상성 배율.

    2타입이면 두 배율을 곱한다. 한쪽이 0이면 결과도 0이다.
    결과는 0 / 0.25 / 0.5 / 1 / 2 / 4 중 하나다.
    """
    if not defending:
        raise ValueError("방어 타입이 최소 하나는 있어야 한다.")
    if len(defending) > MAX_DEFENDING_TYPES:
        raise ValueError(f"방어 타입은 최대 {MAX_DEFENDING_TYPES}개다. 받은 개수: {len(defending)}")

    result = NEUTRAL
    for defending_type in defending:
        result *= chart.multiplier(attacking, defending_type)
    return result
