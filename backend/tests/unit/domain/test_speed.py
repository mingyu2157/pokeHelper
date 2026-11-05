"""스피드 라인과 행동 순서 테스트.

스피드 라인 기대값은 CLAUDE.md 5-1 검산표에서 온다.
`test_stats.py` 의 골든 케이스와 같은 값이므로 교차검증 역할도 한다.
"""

import pytest

from app.domain.nature import NatureModifier
from app.domain.speed import (
    SpeedLine,
    TurnOrder,
    points_to_outspeed,
    resolve_order,
    speed_line,
    speed_lines,
)

# 한카리아스 스피드 종족값
GARCHOMP_BASE_SPEED = 102


class TestSpeedLines:
    """CLAUDE.md 5-1 검산표: 169 / 154 / 122 / 109"""

    @pytest.mark.parametrize(
        ("line", "expected"),
        [
            (SpeedLine.MAX, 169),
            (SpeedLine.SEMI, 154),
            (SpeedLine.UNINVESTED, 122),
            (SpeedLine.MIN, 109),
        ],
    )
    def test_garchomp(self, line, expected):
        assert speed_line(GARCHOMP_BASE_SPEED, line) == expected

    def test_all_lines_at_once(self):
        lines = speed_lines(GARCHOMP_BASE_SPEED)
        assert lines == {
            SpeedLine.MAX: 169,
            SpeedLine.SEMI: 154,
            SpeedLine.UNINVESTED: 122,
            SpeedLine.MIN: 109,
        }

    def test_lines_are_ordered_from_fast_to_slow(self):
        lines = speed_lines(GARCHOMP_BASE_SPEED)
        values = [lines[line] for line in SpeedLine]
        assert values == sorted(values, reverse=True)

    def test_min_line_is_the_floor(self):
        """개체값 0 조정이 불가능하므로 하락보정 + 0포인트가 하한이다 (5-1 귀결 1)."""
        lines = speed_lines(GARCHOMP_BASE_SPEED)
        assert lines[SpeedLine.MIN] == min(lines.values())

    def test_rejects_invalid_base_stat(self):
        from app.domain.exceptions import InvalidBaseStat

        with pytest.raises(InvalidBaseStat):
            speed_line(0, SpeedLine.MAX)


class TestPointsToOutspeed:
    """ev_solver 의 OUTSPEED 제약이 쓸 함수."""

    def test_no_points_needed_when_already_faster(self):
        # 무보정 0포인트가 122. 상대 100이면 투자 없이 넘는다.
        assert points_to_outspeed(GARCHOMP_BASE_SPEED, NatureModifier.NEUTRAL, 100) == 0

    def test_finds_minimum_points(self):
        # 무보정: 122 + p. 상대 130을 넘으려면 131 이상 -> p = 9
        assert points_to_outspeed(GARCHOMP_BASE_SPEED, NatureModifier.NEUTRAL, 130) == 9

    def test_strictly_greater_not_equal(self):
        """동속은 추월이 아니다. 상대 실수치를 '넘어야' 한다."""
        points = points_to_outspeed(GARCHOMP_BASE_SPEED, NatureModifier.NEUTRAL, 130)
        from app.domain.stats import calc_stat

        assert calc_stat(GARCHOMP_BASE_SPEED, points, NatureModifier.NEUTRAL) > 130
        assert calc_stat(GARCHOMP_BASE_SPEED, points - 1, NatureModifier.NEUTRAL) <= 130

    def test_returns_none_when_impossible(self):
        """총합 제약 안에서 해가 없으면 명확히 불가능하다고 알린다 (5-4 규칙 5)."""
        assert points_to_outspeed(GARCHOMP_BASE_SPEED, NatureModifier.NEUTRAL, 999) is None

    def test_rising_nature_reaches_further(self):
        """같은 상대를 상승보정이면 더 적은 포인트로 넘는다."""
        neutral = points_to_outspeed(GARCHOMP_BASE_SPEED, NatureModifier.NEUTRAL, 150)
        rising = points_to_outspeed(GARCHOMP_BASE_SPEED, NatureModifier.UP, 150)
        assert rising is not None and neutral is not None
        assert rising < neutral

    def test_falling_nature_may_be_impossible(self):
        """하락보정으로는 못 넘는 상대를 상승보정으로는 넘는다."""
        assert points_to_outspeed(GARCHOMP_BASE_SPEED, NatureModifier.DOWN, 160) is None
        assert points_to_outspeed(GARCHOMP_BASE_SPEED, NatureModifier.UP, 160) is not None

    def test_never_exceeds_per_stat_maximum(self):
        from app.domain.constants import MAX_POINTS_PER_STAT

        points = points_to_outspeed(GARCHOMP_BASE_SPEED, NatureModifier.UP, 168)
        assert points is not None
        assert points <= MAX_POINTS_PER_STAT


class TestResolveOrder:
    def test_faster_moves_first(self):
        assert resolve_order(169, 154) is TurnOrder.FASTER

    def test_slower_moves_second(self):
        assert resolve_order(154, 169) is TurnOrder.SLOWER

    def test_equal_speed_is_a_tie(self):
        """동속은 50% 확률이다. bool 로 뭉개면 '운'이라는 정보가 사라진다."""
        assert resolve_order(154, 154) is TurnOrder.SPEED_TIE

    def test_rejects_invalid_speed(self):
        with pytest.raises(ValueError):
            resolve_order(0, 100)


class TestTrickRoom:
    """트릭룸은 본가와 동일하다 (2026-11-06 개발자 확인).

    스피드 순서를 뒤집지만 **기술 우선도는 그대로**다.
    """

    def test_slower_moves_first(self):
        assert resolve_order(154, 169, trick_room=True) is TurnOrder.FASTER

    def test_faster_moves_second(self):
        assert resolve_order(169, 154, trick_room=True) is TurnOrder.SLOWER

    def test_tie_stays_a_tie(self):
        assert resolve_order(154, 154, trick_room=True) is TurnOrder.SPEED_TIE

    def test_inverts_the_normal_result(self):
        normal = resolve_order(169, 154)
        inverted = resolve_order(169, 154, trick_room=True)
        assert normal is TurnOrder.FASTER
        assert inverted is TurnOrder.SLOWER


class TestPriority:
    """기술 우선도가 스피드보다 먼저 판정된다. 트릭룸도 이건 못 뒤집는다."""

    def test_higher_priority_wins_despite_lower_speed(self):
        assert resolve_order(100, 200, my_priority=1) is TurnOrder.FASTER

    def test_lower_priority_loses_despite_higher_speed(self):
        assert resolve_order(200, 100, opponent_priority=1) is TurnOrder.SLOWER

    def test_trick_room_does_not_invert_priority(self):
        assert resolve_order(100, 200, my_priority=1, trick_room=True) is TurnOrder.FASTER

    def test_equal_priority_falls_back_to_speed(self):
        assert resolve_order(169, 154, my_priority=1, opponent_priority=1) is TurnOrder.FASTER

    def test_negative_priority(self):
        assert resolve_order(200, 100, my_priority=-1) is TurnOrder.SLOWER

    def test_default_priority_is_zero(self):
        assert resolve_order(169, 154) is resolve_order(169, 154, my_priority=0)
