"""스피드 라인과 행동 순서.

**이미 보정된 실수치를 받는다.** 구애스카프 1.5배 같은 도구 효과는 호출자가 반영한다
(ADR-007). 도메인은 어느 도구가 몇 배인지 모른다.

개체값이 31로 고정이라 **개체값 0으로 스피드를 낮추는 조정이 불가능하다.**
저속 조정의 하한은 하락보정 + 0포인트다 (CLAUDE.md 5-1 귀결 1).
트릭룸·자이로볼 설계에서 기존 시리즈 감각을 그대로 가져오면 안 된다.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import Enum, auto
from typing import Final

from app.domain.constants import MAX_POINTS_PER_STAT, MIN_POINTS_PER_STAT
from app.domain.nature import NatureModifier
from app.domain.stats import calc_stat

NO_PRIORITY: Final[int] = 0


class SpeedLine(Enum):
    """관용적으로 쓰이는 스피드 조정 네 가지. 빠른 쪽부터 나열한다."""

    MAX = auto()
    SEMI = auto()
    UNINVESTED = auto()
    MIN = auto()


# (성격 보정, 투자 포인트)
_SPEED_LINE_SPECS: Final[Mapping[SpeedLine, tuple[NatureModifier, int]]] = {
    SpeedLine.MAX: (NatureModifier.UP, MAX_POINTS_PER_STAT),
    SpeedLine.SEMI: (NatureModifier.NEUTRAL, MAX_POINTS_PER_STAT),
    SpeedLine.UNINVESTED: (NatureModifier.NEUTRAL, MIN_POINTS_PER_STAT),
    SpeedLine.MIN: (NatureModifier.DOWN, MIN_POINTS_PER_STAT),
}


class TurnOrder(Enum):
    """행동 순서 판정.

    동속을 SLOWER 로 뭉개지 않는다. 50% 확률이라는 사실이 사라지면
    "왜 이 선출인가"를 설명할 수 없다.
    """

    FASTER = auto()
    SLOWER = auto()
    SPEED_TIE = auto()


def speed_line(base_stat: int, line: SpeedLine) -> int:
    """해당 조정의 스피드 실수치."""
    modifier, points = _SPEED_LINE_SPECS[line]
    return calc_stat(base_stat, points, modifier)


def speed_lines(base_stat: int) -> dict[SpeedLine, int]:
    """네 조정을 한 번에. 상대 스펙을 가정할 때 쓴다."""
    return {line: speed_line(base_stat, line) for line in SpeedLine}


def points_to_outspeed(
    base_stat: int,
    modifier: NatureModifier,
    target_speed: int,
) -> int | None:
    """상대 실수치를 넘는 데 필요한 최소 포인트. 불가능하면 None.

    **동속은 추월이 아니다.** 상대 실수치를 초과해야 한다.
    판정은 포인트가 아니라 최종 실수치로 한다 — 포인트와 실수치는 선형이 아니다
    (CLAUDE.md 5-1 귀결 2).

    해가 없으면 근사치를 내놓지 않고 None 을 돌려준다 (5-4 규칙 5).
    """
    for points in range(MIN_POINTS_PER_STAT, MAX_POINTS_PER_STAT + 1):
        if calc_stat(base_stat, points, modifier) > target_speed:
            return points
    return None


def resolve_order(
    my_speed: int,
    opponent_speed: int,
    *,
    my_priority: int = NO_PRIORITY,
    opponent_priority: int = NO_PRIORITY,
    trick_room: bool = False,
) -> TurnOrder:
    """누가 먼저 움직이는가.

    우선도를 먼저 보고, 같을 때만 스피드를 본다.
    **트릭룸은 스피드 순서만 뒤집는다. 우선도는 못 뒤집는다.**
    """
    if my_speed <= 0 or opponent_speed <= 0:
        raise ValueError(f"스피드 실수치는 1 이상이어야 한다: {my_speed}, {opponent_speed}")

    if my_priority != opponent_priority:
        return TurnOrder.FASTER if my_priority > opponent_priority else TurnOrder.SLOWER

    if my_speed == opponent_speed:
        return TurnOrder.SPEED_TIE

    i_am_faster = my_speed > opponent_speed
    if trick_room:
        i_am_faster = not i_am_faster
    return TurnOrder.FASTER if i_am_faster else TurnOrder.SLOWER
