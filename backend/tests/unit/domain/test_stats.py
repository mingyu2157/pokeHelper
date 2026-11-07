"""실수치 계산 골든 테스트.

기대값의 출처는 CLAUDE.md 5-1이다.
골든 케이스가 깨지면 기대값을 고치지 말고 구현을 고친다.
"""

import math

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


class TestFloorNotRound:
    """소수점은 버림이다. 반올림이 아니다.

    (종족값+20+포인트)가 10으로 나눈 나머지 5일 때 정확히 .5 가 나온다.
    포인트가 0~32를 훑으므로 모든 포켓몬이 이 경계를 여러 번 지나간다.

    파이썬 기본 round() 는 은행가 반올림이라 27.5->28, 38.5->38 처럼
    일관되지도 않는다. 아래 케이스는 버림과 반올림의 결과가 실제로 갈린다.
    """

    @pytest.mark.parametrize(
        ("base_stat", "modifier", "exact", "floored", "if_rounded"),
        [
            (5, NatureModifier.UP, 27.5, 27, 28),
            (25, NatureModifier.UP, 49.5, 49, 50),
            (45, NatureModifier.UP, 71.5, 71, 72),
            (15, NatureModifier.DOWN, 31.5, 31, 32),
            (35, NatureModifier.DOWN, 49.5, 49, 50),
        ],
    )
    def test_half_is_floored(self, base_stat, modifier, exact, floored, if_rounded):
        result = calc_stat(base_stat, 0, modifier)
        assert result == floored, f"{exact} 는 버림해서 {floored} 여야 한다"
        assert result != if_rounded, "반올림 연산이 들어갔다"

    def test_matches_math_floor_across_full_range(self):
        """종족값 1~255 x 포인트 0~32 x 보정 3종 완전 탐색."""
        numerators = {
            NatureModifier.UP: 11,
            NatureModifier.NEUTRAL: 10,
            NatureModifier.DOWN: 9,
        }
        for base_stat in range(1, 256):
            for effort_points in range(33):
                raw = base_stat + 20 + effort_points
                for modifier, numerator in numerators.items():
                    expected = math.floor(raw * numerator / 10)
                    assert calc_stat(base_stat, effort_points, modifier) == expected, (
                        f"base={base_stat} points={effort_points} {modifier.name}"
                    )


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


class TestMagicNumber:
    """상승보정에서 버림 손실이 0인 지점 (커뮤니티 용어: 매직넘버).

    `종족값 + 20 + 포인트` 가 10의 배수면 `× 11/10` 이 정확히 떨어진다.
    바로 직전에서 멈추면 1포인트로 실수치 2를 얻을 기회를 버린다.
    """

    @pytest.mark.parametrize("points", [8, 18, 28])
    def test_landing_points(self, points):
        from app.domain.stats import is_magic_number

        assert is_magic_number(GARCHOMP_BASE_SPEED, points, NatureModifier.UP)

    @pytest.mark.parametrize("points", [0, 7, 9, 17, 27, 32])
    def test_non_landing_points(self, points):
        from app.domain.stats import is_magic_number

        assert not is_magic_number(GARCHOMP_BASE_SPEED, points, NatureModifier.UP)

    def test_result_is_a_multiple_of_eleven(self):
        from app.domain.stats import is_magic_number

        for points in range(33):
            if is_magic_number(GARCHOMP_BASE_SPEED, points, NatureModifier.UP):
                assert calc_stat(GARCHOMP_BASE_SPEED, points, NatureModifier.UP) % 11 == 0

    def test_only_applies_to_rising_nature(self):
        from app.domain.stats import is_magic_number

        assert not is_magic_number(GARCHOMP_BASE_SPEED, 8, NatureModifier.NEUTRAL)
        assert not is_magic_number(GARCHOMP_BASE_SPEED, 8, NatureModifier.DOWN)

    @pytest.mark.parametrize(
        ("points", "expected"), [(0, 8), (7, 1), (8, 0), (17, 1), (18, 0), (32, None)]
    )
    def test_distance_to_next(self, points, expected):
        from app.domain.stats import points_to_next_magic_number

        assert (
            points_to_next_magic_number(GARCHOMP_BASE_SPEED, points, NatureModifier.UP) == expected
        )

    def test_distance_is_none_without_rising_nature(self):
        from app.domain.stats import points_to_next_magic_number

        assert points_to_next_magic_number(GARCHOMP_BASE_SPEED, 7, NatureModifier.NEUTRAL) is None
        assert points_to_next_magic_number(GARCHOMP_BASE_SPEED, 7, NatureModifier.DOWN) is None

    def test_one_point_before_magic_gains_two(self):
        """매직넘버 직전에서 1포인트를 더 주면 실수치가 2 오른다."""
        from app.domain.stats import points_to_next_magic_number

        for points in range(32):
            if points_to_next_magic_number(GARCHOMP_BASE_SPEED, points, NatureModifier.UP) == 1:
                before = calc_stat(GARCHOMP_BASE_SPEED, points, NatureModifier.UP)
                after = calc_stat(GARCHOMP_BASE_SPEED, points + 1, NatureModifier.UP)
                assert after - before == 2


class TestRankStage:
    """랭크(능력 변화) 배율. 본가와 같다 (2026-11-10 개발자 확인).

    능력치는 (2+n)/2 배, 하락은 2/(2+n) 배.
    명중률·회피율은 (3+n)/3 로 표가 다르지만 데미지 계산에는 쓰지 않는다.

    분수라서 정수 연산으로 정확히 계산할 수 있다.
    """

    @pytest.mark.parametrize(
        ("stage", "expected"),
        [
            (6, 400),
            (5, 350),
            (4, 300),
            (3, 250),
            (2, 200),
            (1, 150),
            (0, 100),
            (-1, 66),
            (-2, 50),
            (-3, 40),
            (-4, 33),
            (-5, 28),
            (-6, 25),
        ],
    )
    def test_matches_the_published_table(self, stage, expected):
        """실수치 100 기준. 위키 표의 퍼센트와 맞아야 한다."""
        from app.domain.stats import apply_rank_stage

        assert apply_rank_stage(100, stage) == expected

    def test_zero_stage_changes_nothing(self):
        from app.domain.stats import apply_rank_stage

        for stat in (1, 57, 154, 332):
            assert apply_rank_stage(stat, 0) == stat

    def test_rounds_down(self):
        from app.domain.stats import apply_rank_stage

        # 101 × 3/2 = 151.5 -> 151
        assert apply_rank_stage(101, 1) == 151
        # 100 × 2/3 = 66.67 -> 66
        assert apply_rank_stage(100, -1) == 66

    def test_never_drops_below_one(self):
        from app.domain.stats import apply_rank_stage

        assert apply_rank_stage(1, -6) == 1

    def test_is_monotonic(self):
        from app.domain.stats import apply_rank_stage

        values = [apply_rank_stage(154, s) for s in range(-6, 7)]
        assert values == sorted(values)

    @pytest.mark.parametrize("stage", [-7, 7, 100])
    def test_rejects_out_of_range_stage(self, stage):
        from app.domain.exceptions import InvalidRankStage
        from app.domain.stats import apply_rank_stage

        with pytest.raises(InvalidRankStage):
            apply_rank_stage(100, stage)

    def test_rejects_invalid_stat(self):
        from app.domain.exceptions import InvalidBaseStat
        from app.domain.stats import apply_rank_stage

        with pytest.raises(InvalidBaseStat):
            apply_rank_stage(0, 1)
