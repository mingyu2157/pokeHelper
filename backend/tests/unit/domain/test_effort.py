"""노력치 배분 검증 테스트.

챔피언스는 포인트 방식이다. 스탯당 0~32, 총합 66 이하가 하드 제약이다.
기존 시리즈의 노력치 개념은 이 프로젝트에 존재하지 않는다.
"""

import pytest

from app.domain.constants import MAX_POINTS_PER_STAT, MAX_TOTAL_POINTS
from app.domain.effort import EffortSpread, validate
from app.domain.exceptions import InvalidEffortSpread
from app.domain.nature import StatKey


class TestConstants:
    def test_rules_match_spec(self):
        assert MAX_POINTS_PER_STAT == 32
        assert MAX_TOTAL_POINTS == 66


class TestValidSpreads:
    def test_empty_spread_is_valid(self):
        validate(EffortSpread())

    def test_canonical_spread_is_valid(self):
        """32 + 32 + 2 가 기본형이다 (총합 66을 정확히 채운다)."""
        spread = EffortSpread(attack=32, speed=32, hp=2)
        assert spread.total == MAX_TOTAL_POINTS
        validate(spread)

    def test_max_in_single_stat_is_valid(self):
        validate(EffortSpread(hp=MAX_POINTS_PER_STAT))

    def test_under_total_is_valid(self):
        validate(EffortSpread(attack=10, speed=10))


class TestPerStatLimit:
    def test_exceeding_per_stat_max_is_rejected(self):
        with pytest.raises(InvalidEffortSpread):
            validate(EffortSpread(speed=MAX_POINTS_PER_STAT + 1))

    def test_negative_points_are_rejected(self):
        with pytest.raises(InvalidEffortSpread):
            validate(EffortSpread(attack=-1))

    def test_error_names_the_offending_stat(self):
        with pytest.raises(InvalidEffortSpread, match="speed"):
            validate(EffortSpread(speed=33))


class TestTotalLimit:
    def test_exceeding_total_is_rejected(self):
        """스탯별로는 전부 합법이지만 총합이 넘는 경우."""
        spread = EffortSpread(hp=32, attack=32, defense=32)
        assert spread.total == 96
        with pytest.raises(InvalidEffortSpread):
            validate(spread)

    def test_total_exactly_at_limit_is_valid(self):
        spread = EffortSpread(hp=32, attack=32, defense=2)
        assert spread.total == MAX_TOTAL_POINTS
        validate(spread)

    def test_one_over_total_is_rejected(self):
        spread = EffortSpread(hp=32, attack=32, defense=3)
        assert spread.total == MAX_TOTAL_POINTS + 1
        with pytest.raises(InvalidEffortSpread):
            validate(spread)


class TestSpreadAccess:
    def test_total_sums_all_stats(self):
        spread = EffortSpread(hp=1, attack=2, defense=3, sp_attack=4, sp_defense=5, speed=6)
        assert spread.total == 21

    def test_points_for_returns_per_stat_value(self):
        spread = EffortSpread(attack=10, speed=32)
        assert spread.points_for(StatKey.ATTACK) == 10
        assert spread.points_for(StatKey.SPEED) == 32
        assert spread.points_for(StatKey.DEFENSE) == 0

    def test_spread_is_immutable(self):
        spread = EffortSpread(attack=10)
        with pytest.raises(AttributeError):
            spread.attack = 20
