"""스탯 키와 성격 보정.

성격 이름(Adamant 등) -> 상승/하락 스탯 매핑은 아직 두지 않는다.
실제로 사용하는 시점에 출처와 교차검증해서 추가한다.
여기에는 계산에 필요한 보정 단계만 있다.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum, auto

from app.domain.exceptions import InvalidNatureSpread, UnknownNature


class StatKey(Enum):
    """스탯 식별자. UI 표기 HABCDS 와 1:1 대응한다."""

    HP = auto()
    ATTACK = auto()
    DEFENSE = auto()
    SP_ATTACK = auto()
    SP_DEFENSE = auto()
    SPEED = auto()


class NatureModifier(Enum):
    """성격 보정 단계.

    float 을 직접 받지 않는 이유는 1.1/1.0/0.9 외의 값이 계산에 들어오는 걸 막기 위해서다.
    """

    UP = auto()
    NEUTRAL = auto()
    DOWN = auto()


@dataclass(frozen=True)
class NatureSpec:
    """성격 하나. 상승·하락은 항상 쌍이거나 둘 다 없다(무보정)."""

    name: str
    raised: StatKey | None
    lowered: StatKey | None

    @property
    def is_neutral(self) -> bool:
        return self.raised is None

    def modifiers(self) -> dict[StatKey, NatureModifier]:
        """BuildTarget 에 넣을 스탯별 보정. 무보정이면 빈 사전."""
        if self.raised is None or self.lowered is None:
            return {}
        return {self.raised: NatureModifier.UP, self.lowered: NatureModifier.DOWN}


@dataclass(frozen=True)
class NatureCatalog:
    """성격 이름 -> 보정. 데이터는 backend/data/natures.json 에 있다.

    domain 은 파일을 읽지 않는다. 바깥 계층이 JSON 을 읽어 주입한다 (ADR-007).
    """

    _specs: Mapping[str, NatureSpec]

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Mapping[str, str | None]]) -> NatureCatalog:
        specs: dict[str, NatureSpec] = {}
        for name, entry in raw.items():
            raised = _parse_stat(name, entry.get("raised"))
            lowered = _parse_stat(name, entry.get("lowered"))
            if (raised is None) != (lowered is None):
                raise InvalidNatureSpread(f"{name}: 성격 보정은 항상 쌍이다 ({raised} / {lowered})")
            if raised is not None and raised is lowered:
                raise InvalidNatureSpread(f"{name}: 같은 스탯을 올리고 내릴 수 없다")
            specs[name] = NatureSpec(name=name, raised=raised, lowered=lowered)
        return cls(_specs=specs)

    def get(self, name: str) -> NatureSpec:
        """정확 일치 조회. 모르는 이름은 추측하지 않고 실패시킨다 (ADR-005)."""
        try:
            return self._specs[name]
        except KeyError as error:
            raise UnknownNature(f"알 수 없는 성격: {name!r}") from error

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._specs))


def _parse_stat(nature_name: str, raw: str | None) -> StatKey | None:
    if raw is None:
        return None
    try:
        return StatKey[raw]
    except KeyError as error:
        raise UnknownNature(f"{nature_name}: 알 수 없는 스탯 {raw!r}") from error
