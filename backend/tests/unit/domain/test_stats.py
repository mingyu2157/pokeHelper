"""실수치 계산 골든 테스트.

기대값의 출처는 CLAUDE.md 5-1이다.
골든 케이스가 깨지면 기대값을 고치지 말고 구현을 고친다.
"""

import pytest

from app.domain.exceptions import InvalidBaseStat
from app.domain.nature import NatureModifier
from app.domain.stats import calc_hp, calc_stat

# 한카리아스 스피드 종족값
GARCHOMP_BASE_SPEED = 102


class TestGoldenSpeedLines:
    """CLAUDE.md 5-1 검산표. 이 4케이스가 이 프로젝트의 첫 마일스톤이다."""

    @pytest.mark.parametrize(
        ("label", "effort_points", "modifier", "expected"),
        [
            ("최속", 32, NatureModifier.UP, 169),
            ("준속", 32, NatureModifier.NEUTRAL, 154),
            ("무보정 0", 0, NatureModifier.NEUTRAL, 122),
            ("최저속", 0, NatureModifier.DOWN, 109),
        ],
    )
    def test_garchomp_speed(self, label, effort_points, modifier, expected):
        assert calc_stat(GARCHOMP_BASE_SPEED, effort_points, modifier) == expected, label


class TestNatureModifierIsAppliedLast:
    """성격 보정은 포인트를 더한 뒤에 곱한다. 순서를 바꾸면 값이 어긋난다."""

    def test_multiply_after_adding_points(self):
        # floor((102 + 20 + 32) * 1.1) = floor(169.4) = 169
        assert calc_stat(GARCHOMP_BASE_SPEED, 32, NatureModifier.UP) == 169

    def test_multiplying_before_adding_would_differ(self):
        # 만약 보정을 먼저 곱했다면 floor(122 * 1.1) + 32 = 134 + 32 = 166 이 나온다.
        # 166 이 나오면 적용 순서가 뒤집힌 것이다.
        assert calc_stat(GARCHOMP_BASE_SPEED, 32, NatureModifier.UP) != 166


class TestIncrementBehaviour:
    """포인트 1당 실수치 증가량의 비선형성.

    노력치 솔버의 판정 기준이 포인트가 아니라 최종 실수치여야 하는 근거다.
    """

    def test_rising_nature_never_fails_to_increase(self):
        """상승보정에서 포인트 1은 반드시 실수치를 1 이상 올린다."""
        for points in range(32):
            before = calc_stat(GARCHOMP_BASE_SPEED, points, NatureModifier.UP)
            after = calc_stat(GARCHOMP_BASE_SPEED, points + 1, NatureModifier.UP)
            assert after - before >= 1, f"points={points} 에서 증가량 0"

    def test_rising_nature_jumps_by_two(self):
        """(종족값+20+포인트)가 9로 끝나면 다음 포인트에서 2가 오른다."""
        # 102 + 20 + 7 = 129 -> 141,  102 + 20 + 8 = 130 -> 143
        assert calc_stat(GARCHOMP_BASE_SPEED, 7, NatureModifier.UP) == 141
        assert calc_stat(GARCHOMP_BASE_SPEED, 8, NatureModifier.UP) == 143

    def test_falling_nature_can_stall(self):
        """하락보정에서는 포인트를 줘도 실수치가 그대로인 구간이 있다.

        (종족값+20+포인트)가 10의 배수일 때 발생한다.
        """
        # 종족값 80 -> 80 + 20 + 0 = 100 (10의 배수)
        assert calc_stat(80, 0, NatureModifier.DOWN) == 90
        assert calc_stat(80, 1, NatureModifier.DOWN) == 90  # 포인트를 줘도 그대로

    def test_neutral_nature_is_always_linear(self):
        for points in range(32):
            before = calc_stat(GARCHOMP_BASE_SPEED, points, NatureModifier.NEUTRAL)
            after = calc_stat(GARCHOMP_BASE_SPEED, points + 1, NatureModifier.NEUTRAL)
            assert after - before == 1


class TestHp:
    """HP는 성격 보정을 받지 않는다."""

    @pytest.mark.parametrize(
        ("base_stat", "effort_points", "expected"),
        [
            (108, 0, 183),
            (108, 32, 215),
            (108, 4, 187),
        ],
    )
    def test_hp_formula(self, base_stat, effort_points, expected):
        assert calc_hp(base_stat, effort_points) == expected

    def test_hp_is_linear_in_points(self):
        for points in range(32):
            assert calc_hp(108, points + 1) - calc_hp(108, points) == 1


class TestShedinjaGuard:
    """HP 종족값 1은 껍질몬뿐이고 챔피언스에 존재하지 않는다.

    이 값이 들어오면 시드 데이터 오류이므로 조용히 계산하지 않고 실패시킨다.
    축약 공식을 그대로 적용하면 1 + 75 = 76 이라는 틀린 값이 나간다.
    """

    def test_base_hp_one_is_rejected(self):
        with pytest.raises(InvalidBaseStat):
            calc_hp(1, 0)

    def test_normal_low_hp_is_accepted(self):
        assert calc_hp(20, 0) == 95


class TestInputValidation:
    def test_negative_base_stat_is_rejected(self):
        with pytest.raises(InvalidBaseStat):
            calc_stat(-1, 0, NatureModifier.NEUTRAL)

    def test_zero_base_stat_is_rejected(self):
        with pytest.raises(InvalidBaseStat):
            calc_stat(0, 0, NatureModifier.NEUTRAL)
