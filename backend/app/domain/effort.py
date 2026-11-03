"""노력치 배분 — 챔피언스는 포인트 방식이다.

스탯당 0~32, 총합 66 이하가 하드 제약이다.
이 검증은 파티 저장 시점과 계산 진입 시점 양쪽에서 호출한다.
불가능한 배분으로 계산한 수치를 사용자에게 보여주는 건 계산 오류보다 나쁘다.
"""

from dataclasses import dataclass, fields

from app.domain.constants import (
    MAX_POINTS_PER_STAT,
    MAX_TOTAL_POINTS,
    MIN_POINTS_PER_STAT,
)
from app.domain.exceptions import InvalidEffortSpread
from app.domain.nature import StatKey

_STAT_FIELD_NAMES: dict[StatKey, str] = {
    StatKey.HP: "hp",
    StatKey.ATTACK: "attack",
    StatKey.DEFENSE: "defense",
    StatKey.SP_ATTACK: "sp_attack",
    StatKey.SP_DEFENSE: "sp_defense",
    StatKey.SPEED: "speed",
}


@dataclass(frozen=True)
class EffortSpread:
    """한 포켓몬의 노력치 배분. UI 표기 HABCDS 순서를 따른다."""

    hp: int = 0
    attack: int = 0
    defense: int = 0
    sp_attack: int = 0
    sp_defense: int = 0
    speed: int = 0

    @property
    def total(self) -> int:
        return sum(getattr(self, field.name) for field in fields(self))

    def points_for(self, stat: StatKey) -> int:
        value: int = getattr(self, _STAT_FIELD_NAMES[stat])
        return value


def validate(spread: EffortSpread) -> None:
    """각 스탯 0~32, 총합 66 이하. 위반 시 InvalidEffortSpread 를 발생시킨다."""
    for stat, field_name in _STAT_FIELD_NAMES.items():
        points: int = getattr(spread, field_name)
        if points < MIN_POINTS_PER_STAT or points > MAX_POINTS_PER_STAT:
            raise InvalidEffortSpread(
                f"{field_name}: {points} 포인트는 허용 범위를 벗어났다 "
                f"({MIN_POINTS_PER_STAT}~{MAX_POINTS_PER_STAT}). stat={stat.name}"
            )

    if spread.total > MAX_TOTAL_POINTS:
        raise InvalidEffortSpread(
            f"총합 {spread.total} 포인트는 상한 {MAX_TOTAL_POINTS} 를 초과한다."
        )
