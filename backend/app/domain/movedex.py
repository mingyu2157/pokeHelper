"""기술 마스터 시드 조회.

데이터는 `backend/data/moves.json` 에 있고 생성 스크립트가 만든다.
**domain 은 파일을 읽지 않는다** (ADR-007) — 바깥 계층이 읽어 주입한다.

★ 여기 담긴 수치는 본가 값이 아니다 ★

챔피언스는 밸런스 패치로 위력·명중을 조정하고 PP 를 {1, 8, 12, 16, 20} 로
정규화했다. 본가 데이터로 대체하면 조용히 틀린다 (ADR-009).
`scripts/generate_moves.py` 의 설명 참조.

`power=None` 의 의미가 두 가지라 구분이 필요하다:
  - 변화 기술      — 데미지를 주지 않는다
  - 변동 위력 기술  — 데미지는 주는데 위력이 상황에 따라 정해진다 (자이로볼, 카운터...)
둘 다 `MoveSpec` 으로 바로 못 넘긴다. 섞어서 0 으로 처리하면 안 되므로 서로 다른
예외로 막는다.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from app.domain.exceptions import (
    MoveHasNoFixedPower,
    MoveIsNotDamaging,
    UnknownMove,
    UnknownMoveCategory,
    UnknownPokemonType,
)
from app.domain.models import MoveCategory, MoveSpec
from app.domain.typechart import PokemonType

_CATEGORY_BY_NAME: Mapping[str, MoveCategory] = {
    "physical": MoveCategory.PHYSICAL,
    "special": MoveCategory.SPECIAL,
    "status": MoveCategory.STATUS,
}


@dataclass(frozen=True)
class MoveEntry:
    """기술 하나. 한국어 명칭은 여기 없다 (ADR-005, name_map_moves.json 소관)."""

    move_id: str
    slug: str
    move_type: PokemonType
    category: MoveCategory
    power: int | None
    accuracy: int | None
    pp: int
    priority: int
    contact: bool
    flags: frozenset[str]
    target: str

    @property
    def is_damaging(self) -> bool:
        return self.category is not MoveCategory.STATUS

    @property
    def has_variable_power(self) -> bool:
        """데미지는 주지만 고정 위력이 없는 기술인가."""
        return self.is_damaging and self.power is None

    @property
    def never_misses(self) -> bool:
        """명중 판정이 없는 기술인가. 변화 기술도 여기 포함된다."""
        return self.accuracy is None

    def has_flag(self, flag: str) -> bool:
        return flag in self.flags

    def to_move_spec(self, power: int | None = None) -> MoveSpec:
        """데미지 계산용 `MoveSpec` 으로 변환한다.

        변동 위력 기술은 `power` 를 받아야 한다. 안 주면 추측하지 않고 실패한다.
        """
        if not self.is_damaging:
            raise MoveIsNotDamaging(f"변화 기술로는 데미지를 계산할 수 없다: {self.slug!r}")

        resolved = power if power is not None else self.power
        if resolved is None:
            raise MoveHasNoFixedPower(
                f"{self.slug!r} 는 위력이 상황에 따라 결정된다. 호출자가 계산한 위력을 넘겨야 한다."
            )
        return MoveSpec(power=resolved, move_type=self.move_type, category=self.category)


@dataclass(frozen=True)
class MoveDex:
    """기술 id -> 기술. id 는 영숫자만 남긴 소문자다 (사용률 API 의 영문명과 조인한다)."""

    _entries: Mapping[str, MoveEntry]

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Mapping[str, Any]]) -> MoveDex:
        entries: dict[str, MoveEntry] = {}
        for move_id, record in raw.items():
            entries[move_id] = MoveEntry(
                move_id=move_id,
                slug=str(record["slug"]),
                move_type=_parse_type(move_id, str(record["type"])),
                category=_parse_category(move_id, str(record["category"])),
                power=_optional_int(record["power"]),
                accuracy=_optional_int(record["accuracy"]),
                pp=int(record["pp"]),
                priority=int(record["priority"]),
                contact=bool(record["contact"]),
                flags=frozenset(str(flag) for flag in record["flags"]),
                target=str(record["target"]),
            )
        return cls(_entries=entries)

    def get(self, move_id: str) -> MoveEntry:
        """정확 일치 조회. 모르는 id 는 추측하지 않고 실패시킨다 (ADR-005)."""
        try:
            return self._entries[move_id]
        except KeyError as error:
            raise UnknownMove(f"마스터 시드에 없는 기술: {move_id!r}") from error

    def ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._entries))

    def with_flag(self, flag: str) -> tuple[MoveEntry, ...]:
        """특성 보정 그룹 조회용. 예: '예리함' 은 slicing 기술의 위력을 올린다."""
        return tuple(entry for _, entry in sorted(self._entries.items()) if entry.has_flag(flag))


def _parse_type(move_id: str, raw: str) -> PokemonType:
    try:
        return PokemonType(raw)
    except ValueError as error:
        raise UnknownPokemonType(f"{move_id}: 알 수 없는 타입 {raw!r}") from error


def _parse_category(move_id: str, raw: str) -> MoveCategory:
    try:
        return _CATEGORY_BY_NAME[raw]
    except KeyError as error:
        raise UnknownMoveCategory(f"{move_id}: 알 수 없는 분류 {raw!r}") from error


def _optional_int(raw: object) -> int | None:
    """null 과 0 을 구분해 유지한다. 둘을 섞으면 '모른다'가 '0'이 된다."""
    if raw is None:
        return None
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise TypeError(f"정수 또는 null 이어야 한다: {raw!r}")
    return raw
