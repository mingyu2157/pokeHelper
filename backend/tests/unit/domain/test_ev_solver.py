"""노력치 미세조정 솔버 테스트.

CLAUDE.md 5-4 의 6개 규칙을 테스트로 고정한다. 특히:
- 규칙 3: 판정은 포인트가 아니라 최종 실수치
- 규칙 5: 해가 없으면 근사치 대신 불가능을 명확히 반환하고 충돌을 알린다
- 규칙 6: "이 포인트가 무엇을 위한 것인지"를 제약과 함께 돌려준다
"""

import json
from pathlib import Path

import pytest

from app.domain.constants import MAX_POINTS_PER_STAT, MAX_TOTAL_POINTS
from app.domain.ev_solver import (
    BuildTarget,
    Infeasible,
    KnockoutConstraint,
    OutspeedConstraint,
    Solution,
    SurviveConstraint,
    solve,
)
from app.domain.models import Attacker, BaseStats, Defender, MoveCategory, MoveSpec
from app.domain.nature import NatureModifier, StatKey
from app.domain.stats import calc_stat
from app.domain.typechart import PokemonType, TypeChart

TYPECHART_PATH = Path(__file__).resolve().parents[3] / "data" / "typechart.json"

# 한카리아스 종족값
GARCHOMP = BaseStats(hp=108, attack=130, defense=95, sp_attack=80, sp_defense=85, speed=102)


@pytest.fixture(scope="module")
def chart():
    with TYPECHART_PATH.open(encoding="utf-8") as file:
        return TypeChart.from_mapping(json.load(file)["chart"])


def target(natures=None, base=GARCHOMP, types=(PokemonType.DRAGON, PokemonType.GROUND)):
    return BuildTarget(base_stats=base, natures=natures or {}, types=types)


def physical(power=100, move_type=PokemonType.GROUND):
    return MoveSpec(power=power, move_type=move_type, category=MoveCategory.PHYSICAL)


def ice_move(power=50):
    """드래곤/땅 상대로 4배. 내구 투자를 강제할 만큼 아픈 기술."""
    return MoveSpec(power=power, move_type=PokemonType.ICE, category=MoveCategory.PHYSICAL)


def opponent_attacker(attack=150, types=(PokemonType.NORMAL,)):
    return Attacker(attack_stat=attack, special_attack_stat=attack, types=types)


def opponent_defender(defense=100, hp=180, types=(PokemonType.NORMAL,)):
    return Defender(defense_stat=defense, special_defense_stat=defense, hp_stat=hp, types=types)


class TestOutspeed:
    def test_finds_minimum_points(self, chart):
        # 무보정 스피드: 102+20 = 122. 상대 130 추월 -> 9포인트
        result = solve(chart, target(), [OutspeedConstraint("상대 130", 130)])
        assert isinstance(result, Solution)
        assert result.spread.speed == 9

    def test_no_investment_when_already_faster(self, chart):
        result = solve(chart, target(), [OutspeedConstraint("상대 100", 100)])
        assert isinstance(result, Solution)
        assert result.spread.speed == 0

    def test_uses_declared_nature(self, chart):
        """판정은 포인트가 아니라 최종 실수치다 (규칙 3)."""
        rising = target(natures={StatKey.SPEED: NatureModifier.UP})
        result = solve(chart, rising, [OutspeedConstraint("상대 150", 150)])
        assert isinstance(result, Solution)
        actual = calc_stat(GARCHOMP.speed, result.spread.speed, NatureModifier.UP)
        assert actual > 150
        previous = calc_stat(GARCHOMP.speed, result.spread.speed - 1, NatureModifier.UP)
        assert previous <= 150

    def test_impossible_speed_is_infeasible(self, chart):
        result = solve(chart, target(), [OutspeedConstraint("불가능", 999)])
        assert isinstance(result, Infeasible)


class TestKnockout:
    def test_invests_in_attack(self, chart):
        constraint = KnockoutConstraint(
            "상대 확정 1타", opponent=opponent_defender(hp=100), move=physical()
        )
        result = solve(chart, target(), [constraint])
        assert isinstance(result, Solution)
        assert result.spread.attack == 25
        assert result.spread.sp_attack == 0

    def test_special_move_invests_in_special_attack(self, chart):
        move = MoveSpec(power=100, move_type=PokemonType.DRAGON, category=MoveCategory.SPECIAL)
        constraint = KnockoutConstraint(
            "특수 확정 1타", opponent=opponent_defender(hp=250, defense=80), move=move
        )
        result = solve(chart, target(), [constraint])
        if isinstance(result, Solution):
            assert result.spread.attack == 0

    def test_unreachable_knockout_is_infeasible(self, chart):
        constraint = KnockoutConstraint(
            "너무 단단함",
            opponent=opponent_defender(hp=9999, defense=500),
            move=physical(power=10),
        )
        result = solve(chart, target(), [constraint])
        assert isinstance(result, Infeasible)


class TestSurvive:
    def test_invests_in_bulk(self, chart):
        constraint = SurviveConstraint(
            "강한 물리 기술 1회 버티기",
            opponent=opponent_attacker(attack=200, types=(PokemonType.ICE,)),
            move=ice_move(),
        )
        result = solve(chart, target(), [constraint])
        assert isinstance(result, Solution)
        assert result.spread.hp + result.spread.defense > 0

    def test_physical_move_does_not_touch_special_defense(self, chart):
        constraint = SurviveConstraint(
            "물리",
            opponent=opponent_attacker(attack=200, types=(PokemonType.ICE,)),
            move=ice_move(),
        )
        result = solve(chart, target(), [constraint])
        assert isinstance(result, Solution)
        assert result.spread.sp_defense == 0

    def test_picks_the_cheapest_combination(self, chart):
        """②번 결정 A안 — 총 투자가 가장 적은 배분을 고른다.

        HP 와 방어 어느 쪽에 실어도 제약을 만족할 수 있으므로,
        기준이 없으면 임의의 답이 나온다. 총 투자 최소가 기본 기준이고
        동점일 때만 HP 를 우선한다 (HP 는 물리·특수 양쪽에 작용한다).
        """
        constraint = SurviveConstraint(
            "버티기",
            opponent=opponent_attacker(attack=200, types=(PokemonType.ICE,)),
            move=ice_move(),
        )
        result = solve(chart, target(), [constraint])
        assert isinstance(result, Solution)
        # 총 투자가 최소인 배분이어야 한다. HP 우선은 동점일 때만 적용된다.
        assert result.spread.total == 36

    def test_already_survives_needs_nothing(self, chart):
        constraint = SurviveConstraint(
            "약한 기술", opponent=opponent_attacker(attack=50), move=physical(power=10)
        )
        result = solve(chart, target(), [constraint])
        assert isinstance(result, Solution)
        assert result.spread.total == 0

    def test_mentions_the_alternative_axis(self, chart):
        """②번 — 솔버가 정하되, 사용자가 축을 직접 고를 수 있음을 알린다."""
        constraint = SurviveConstraint(
            "버티기",
            opponent=opponent_attacker(attack=200, types=(PokemonType.ICE,)),
            move=ice_move(),
        )
        result = solve(chart, target(), [constraint])
        assert isinstance(result, Solution)
        assert any("방어" in note or "HP" in note for note in result.alternatives)


class TestJustifications:
    """규칙 6 — 포인트가 무엇을 위한 것인지 돌려준다."""

    def test_every_invested_stat_has_a_reason(self, chart):
        result = solve(chart, target(), [OutspeedConstraint("상대 130", 130)])
        assert isinstance(result, Solution)
        invested = {stat for stat in StatKey if result.spread.points_for(stat) > 0}
        explained = {j.stat for j in result.justifications}
        assert invested <= explained

    def test_justification_names_the_constraint(self, chart):
        result = solve(chart, target(), [OutspeedConstraint("최속 마스카나", 150)])
        assert isinstance(result, Solution)
        assert any("최속 마스카나" in j.constraint_label for j in result.justifications)

    def test_justification_carries_the_resulting_stat(self, chart):
        result = solve(chart, target(), [OutspeedConstraint("상대 130", 130)])
        assert isinstance(result, Solution)
        speed = next(j for j in result.justifications if j.stat is StatKey.SPEED)
        assert "131" in speed.detail


class TestBudget:
    def test_respects_total_budget(self, chart):
        constraints = [
            OutspeedConstraint("빠른 상대", 150),
            SurviveConstraint(
                "버티기",
                opponent=opponent_attacker(attack=200, types=(PokemonType.ICE,)),
                move=ice_move(),
            ),
        ]
        result = solve(chart, target(), constraints)
        if isinstance(result, Solution):
            assert result.spread.total <= MAX_TOTAL_POINTS

    def test_respects_per_stat_maximum(self, chart):
        result = solve(chart, target(), [OutspeedConstraint("상대 150", 150)])
        assert isinstance(result, Solution)
        for stat in StatKey:
            assert result.spread.points_for(stat) <= MAX_POINTS_PER_STAT

    def test_empty_constraints_give_empty_spread(self, chart):
        result = solve(chart, target(), [])
        assert isinstance(result, Solution)
        assert result.spread.total == 0


class TestConflictReporting:
    """규칙 5 — 불가능하면 근사치를 내놓지 않는다. 무엇이 충돌하는지 알린다."""

    @pytest.fixture
    def conflicting(self):
        """셋 다 각각은 가능하다. 29 + 36 + 25 = 90 으로 예산 66 을 넘길 뿐이다."""
        return [
            OutspeedConstraint("빠른 상대 150", 150),
            SurviveConstraint(
                "얼음 기술 버티기",
                opponent=opponent_attacker(attack=200, types=(PokemonType.ICE,)),
                move=ice_move(),
            ),
            KnockoutConstraint(
                "상대 확정 1타",
                opponent=opponent_defender(hp=100),
                move=physical(),
            ),
        ]

    def test_reports_infeasible(self, chart, conflicting):
        result = solve(chart, target(), conflicting)
        assert isinstance(result, Infeasible)

    def test_reports_budget_gap(self, chart, conflicting):
        result = solve(chart, target(), conflicting)
        assert isinstance(result, Infeasible)
        assert result.budget == MAX_TOTAL_POINTS
        assert result.total_required > MAX_TOTAL_POINTS

    def test_reports_cost_per_constraint(self, chart, conflicting):
        result = solve(chart, target(), conflicting)
        assert isinstance(result, Infeasible)
        assert len(result.per_constraint_points) == len(conflicting)
        assert result.impossible == ()

    def test_separates_impossible_from_expensive(self, chart):
        """어떤 투자로도 불가능한 제약과, 비싸서 못 넣는 제약은 다르다.

        둘을 같은 숫자로 뭉개면 "0포인트면 되는데 왜 안 되지?" 가 된다.
        """
        result = solve(
            chart,
            target(),
            [OutspeedConstraint("상한 초과", 999), OutspeedConstraint("가능", 130)],
        )
        assert isinstance(result, Infeasible)
        assert result.impossible == ("상한 초과",)
        assert result.per_constraint_points == {"가능": 9}

    def test_offers_options_the_user_can_choose_from(self, chart, conflicting):
        """③번 — 포기할 조합을 근거와 함께 보여준다."""
        result = solve(chart, target(), conflicting)
        assert isinstance(result, Infeasible)
        assert len(result.options) > 0
        for option in result.options:
            assert option.spread.total <= MAX_TOTAL_POINTS
            assert len(option.satisfied) > 0
            assert len(option.dropped) > 0

    def test_options_explain_what_is_gained(self, chart, conflicting):
        result = solve(chart, target(), conflicting)
        assert isinstance(result, Infeasible)
        for option in result.options:
            assert len(option.justifications) > 0

    def test_options_are_maximal(self, chart, conflicting):
        """어떤 선택지도 다른 선택지의 부분집합이면 안 된다."""
        result = solve(chart, target(), conflicting)
        assert isinstance(result, Infeasible)
        sets = [frozenset(option.satisfied) for option in result.options]
        for a in sets:
            assert not any(a < b for b in sets)

    def test_single_impossible_constraint_has_no_options(self, chart):
        """하나뿐인 제약이 자체로 불가능하면 제시할 대안이 없다."""
        result = solve(chart, target(), [OutspeedConstraint("불가능", 999)])
        assert isinstance(result, Infeasible)
        assert result.impossible == ("불가능",)
        assert result.options == ()


class TestTiebreak:
    """②번 A안의 동점 규칙.

    자연스럽게 동점이 나오는 시나리오를 만들기 어려워 헬퍼를 직접 검증한다.
    총 투자가 같을 때 HP 쪽에 실린 배분을 고른다 — HP 는 물리·특수 양쪽에 작용한다.
    """

    def test_hp_wins_when_totals_tie(self):
        from app.domain.ev_solver import _prefers_hp

        hp_heavy = {StatKey.HP: 20, StatKey.DEFENSE: 10}
        defense_heavy = {StatKey.HP: 10, StatKey.DEFENSE: 20}
        assert _prefers_hp(hp_heavy, defense_heavy) is True
        assert _prefers_hp(defense_heavy, hp_heavy) is False

    def test_equal_hp_is_not_preferred(self):
        from app.domain.ev_solver import _prefers_hp

        a = {StatKey.HP: 10, StatKey.DEFENSE: 10}
        b = {StatKey.HP: 10, StatKey.SP_DEFENSE: 10}
        assert _prefers_hp(a, b) is False


class TestOptionsRespectBudget:
    """부분집합이라고 다 되는 게 아니다. 둘만 골라도 예산을 넘을 수 있다."""

    @pytest.fixture
    def three_costly(self):
        # 추월 9 / 확정1타 32 / 버티기 36 = 77 > 66
        # 뒤의 둘만 골라도 32 + 36 = 68 로 예산을 넘는다.
        return [
            OutspeedConstraint("추월 130", 130),
            KnockoutConstraint("확정 1타", opponent=opponent_defender(hp=104), move=physical()),
            SurviveConstraint(
                "얼음 버티기",
                opponent=opponent_attacker(attack=200, types=(PokemonType.ICE,)),
                move=ice_move(),
            ),
        ]

    def test_over_budget_subset_is_excluded(self, chart, three_costly):
        result = solve(chart, target(), three_costly)
        assert isinstance(result, Infeasible)
        satisfied_sets = [frozenset(option.satisfied) for option in result.options]
        assert frozenset({"확정 1타", "얼음 버티기"}) not in satisfied_sets

    def test_feasible_subsets_are_offered(self, chart, three_costly):
        result = solve(chart, target(), three_costly)
        assert isinstance(result, Infeasible)
        satisfied_sets = [frozenset(option.satisfied) for option in result.options]
        assert frozenset({"추월 130", "확정 1타"}) in satisfied_sets
        assert frozenset({"추월 130", "얼음 버티기"}) in satisfied_sets


class TestResultIsImmutable:
    def test_solution_is_frozen(self, chart):
        result = solve(chart, target(), [OutspeedConstraint("상대 130", 130)])
        assert isinstance(result, Solution)
        with pytest.raises(AttributeError):
            result.spread = None
