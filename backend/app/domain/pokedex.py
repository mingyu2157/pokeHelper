"""포켓몬 마스터 시드 조회.

데이터는 `backend/data/pokemon.json` 에 있고 생성 스크립트가 만든다.
**domain 은 파일을 읽지 않는다** (ADR-007) — 바깥 계층이 읽어 주입한다.

여기 담긴 `base_stats` 는 **진짜 종족값**이다. championsbattledata 가 주는
값은 노력치 0·무보정 실수치라 오프셋이 더해져 있고, 생성기가 벗겨낸다.
자세한 내용은 `scripts/generate_pokemon.py` 의 설명 참조.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from app.domain.exceptions import UnknownPokemon, UnknownPokemonType
from app.domain.models import BaseStats
from app.domain.typechart import PokemonType


@dataclass(frozen=True)
class PokemonSpec:
    """계산에 필요한 포켓몬 정보. 한국어 명칭은 여기 없다 (ADR-005)."""

    showdown_id: str
    name: str
    display_name: str
    form_kind: str
    types: tuple[PokemonType, ...]
    base_stats: BaseStats
    abilities: tuple[str, ...]


@dataclass(frozen=True)
class Pokedex:
    """showdownId -> 포켓몬. 폼도 각각 별개 항목이다 (CLAUDE.md 8-1)."""

    _specs: Mapping[str, PokemonSpec]

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Mapping[str, Any]]) -> Pokedex:
        specs: dict[str, PokemonSpec] = {}
        for showdown_id, record in raw.items():
            specs[showdown_id] = PokemonSpec(
                showdown_id=showdown_id,
                name=str(record["name"]),
                display_name=str(record["display_name"]),
                form_kind=str(record["form_kind"]),
                types=tuple(_parse_type(showdown_id, t) for t in record["types"]),
                base_stats=BaseStats(**record["base_stats"]),
                abilities=tuple(record["abilities"]),
            )
        return cls(_specs=specs)

    def get(self, showdown_id: str) -> PokemonSpec:
        """정확 일치 조회. 모르는 id 는 추측하지 않고 실패시킨다 (ADR-005)."""
        try:
            return self._specs[showdown_id]
        except KeyError as error:
            raise UnknownPokemon(f"마스터 시드에 없는 포켓몬: {showdown_id!r}") from error

    def ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._specs))


def _parse_type(showdown_id: str, raw: str) -> PokemonType:
    try:
        return PokemonType(raw)
    except ValueError as error:
        raise UnknownPokemonType(f"{showdown_id}: 알 수 없는 타입 {raw!r}") from error
