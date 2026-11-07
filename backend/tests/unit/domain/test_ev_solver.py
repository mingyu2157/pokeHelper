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
from app.domain.damage import Weather
from app.domain.ev_solver import (
    AllocationPriority,
    BattleContext,
    BuildTarget,
    Infeasible,
    KnockoutConstraint,
    OutspeedConstraint,
    Solution,
    SurviveConstraint,
    solve,
)
from app.domain.items import ItemCatalog
from app.domain.models import Attacker, BaseStats, Defender, MoveCategory, MoveSpec
from app.domain.nature import NatureModifier, StatKey
from app.domain.stats import calc_stat
from app.domain.typechart import PokemonType, TypeChart

TYPECHART_PATH = Path(__file__).resolve().parents[3] / "data" / "typechart.json"
ITEMS_PATH = Path(__file__).resolve().parents[3] / "data" / "items.json"

# 한카리아스 종족값
GARCHOMP = BaseStats(hp=108, attack=130, defense=95, sp_attack=80, sp_defense=85, speed=102)


@pytest.fixture(scope="module")
def chart():
    with TYPECHART_PATH.open(encoding="utf-8") as file:
        return TypeChart.from_mapping(json.load(file)["chart"])


@pytest.fixture(scope="module")
def catalog():
    with ITEMS_PATH.open(encoding="utf-8") as file:
        return ItemCatalog.from_mapping(json.load(file)["items"])


def target(
    natures=None,
    base=GARCHOMP,
    types=(PokemonType.DRAGON, PokemonType.GROUND),
    item_id=None,
    rank_stages=None,
):
    return BuildTarget(
        base_stats=base,
        natures=natures or {},
        types=types,
        item_id=item_id,
        rank_stages=rank_stages or {},
    )


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
        result = solve(chart, catalog, target(), [OutspeedConstraint("상대 130", 130)])
        assert isinstance(result, Solution)
        assert result.spread.speed == 9

    def test_no_investment_when_already_faster(self, chart):
        result = solve(chart, catalog, target(), [OutspeedConstraint("상대 100", 100)])
        assert isinstance(result, Solution)
        assert result.spread.speed == 0

    def test_uses_declared_nature(self, chart):
        """판정은 포인트가 아니라 최종 실수치다 (규칙 3)."""
        rising = target(
            natures={
                StatKey.SPEED: NatureModifier.UP,
                StatKey.SP_ATTACK: NatureModifier.DOWN,
            }
        )
        result = solve(chart, catalog, rising, [OutspeedConstraint("상대 150", 150)])
        assert isinstance(result, Solution)
        actual = calc_stat(GARCHOMP.speed, result.spread.speed, NatureModifier.UP)
        assert actual > 150
        previous = calc_stat(GARCHOMP.speed, result.spread.speed - 1, NatureModifier.UP)
        assert previous <= 150

    def test_impossible_speed_is_infeasible(self, chart):
        result = solve(chart, catalog, target(), [OutspeedConstraint("불가능", 999)])
        assert isinstance(result, Infeasible)


class TestKnockout:
    def test_invests_in_attack(self, chart):
        constraint = KnockoutConstraint(
            "상대 확정 1타", opponent=opponent_defender(hp=100), move=physical()
        )
        result = solve(chart, catalog, target(), [constraint])
        assert isinstance(result, Solution)
        assert result.spread.attack == 25
        assert result.spread.sp_attack == 0

    def test_special_move_invests_in_special_attack(self, chart):
        move = MoveSpec(power=100, move_type=PokemonType.DRAGON, category=MoveCategory.SPECIAL)
        constraint = KnockoutConstraint(
            "특수 확정 1타", opponent=opponent_defender(hp=250, defense=80), move=move
        )
        result = solve(chart, catalog, target(), [constraint])
        if isinstance(result, Solution):
            assert result.spread.attack == 0

    def test_unreachable_knockout_is_infeasible(self, chart):
        constraint = KnockoutConstraint(
            "너무 단단함",
            opponent=opponent_defender(hp=9999, defense=500),
            move=physical(power=10),
        )
        result = solve(chart, catalog, target(), [constraint])
        assert isinstance(result, Infeasible)


class TestSurvive:
    def test_invests_in_bulk(self, chart):
        constraint = SurviveConstraint(
            "강한 물리 기술 1회 버티기",
            opponent=opponent_attacker(attack=200, types=(PokemonType.ICE,)),
            move=ice_move(),
        )
        result = solve(chart, catalog, target(), [constraint])
        assert isinstance(result, Solution)
        assert result.spread.hp + result.spread.defense > 0

    def test_physical_move_does_not_touch_special_defense(self, chart):
        constraint = SurviveConstraint(
            "물리",
            opponent=opponent_attacker(attack=200, types=(PokemonType.ICE,)),
            move=ice_move(),
        )
        result = solve(chart, catalog, target(), [constraint])
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
        result = solve(chart, catalog, target(), [constraint])
        assert isinstance(result, Solution)
        # 총 투자가 최소인 배분이어야 한다. HP 우선은 동점일 때만 적용된다.
        assert result.spread.total == 36

    def test_already_survives_needs_nothing(self, chart):
        constraint = SurviveConstraint(
            "약한 기술", opponent=opponent_attacker(attack=50), move=physical(power=10)
        )
        result = solve(chart, catalog, target(), [constraint])
        assert isinstance(result, Solution)
        assert result.spread.total == 0

    def test_mentions_the_alternative_axis(self, chart):
        """②번 — 솔버가 정하되, 사용자가 축을 직접 고를 수 있음을 알린다."""
        constraint = SurviveConstraint(
            "버티기",
            opponent=opponent_attacker(attack=200, types=(PokemonType.ICE,)),
            move=ice_move(),
        )
        result = solve(chart, catalog, target(), [constraint])
        assert isinstance(result, Solution)
        assert any("방어" in note or "HP" in note for note in result.alternatives)


class TestJustifications:
    """규칙 6 — 포인트가 무엇을 위한 것인지 돌려준다."""

    def test_every_invested_stat_has_a_reason(self, chart):
        result = solve(chart, catalog, target(), [OutspeedConstraint("상대 130", 130)])
        assert isinstance(result, Solution)
        invested = {stat for stat in StatKey if result.spread.points_for(stat) > 0}
        explained = {j.stat for j in result.justifications}
        assert invested <= explained

    def test_justification_names_the_constraint(self, chart):
        result = solve(chart, catalog, target(), [OutspeedConstraint("최속 마스카나", 150)])
        assert isinstance(result, Solution)
        assert any("최속 마스카나" in j.constraint_label for j in result.justifications)

    def test_justification_carries_the_resulting_stat(self, chart):
        result = solve(chart, catalog, target(), [OutspeedConstraint("상대 130", 130)])
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
        result = solve(chart, catalog, target(), constraints)
        if isinstance(result, Solution):
            assert result.spread.total <= MAX_TOTAL_POINTS

    def test_respects_per_stat_maximum(self, chart):
        result = solve(chart, catalog, target(), [OutspeedConstraint("상대 150", 150)])
        assert isinstance(result, Solution)
        for stat in StatKey:
            assert result.spread.points_for(stat) <= MAX_POINTS_PER_STAT

    def test_empty_constraints_give_empty_spread(self, chart):
        result = solve(chart, catalog, target(), [])
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
        result = solve(chart, catalog, target(), conflicting)
        assert isinstance(result, Infeasible)

    def test_reports_budget_gap(self, chart, conflicting):
        result = solve(chart, catalog, target(), conflicting)
        assert isinstance(result, Infeasible)
        assert result.budget == MAX_TOTAL_POINTS
        assert result.total_required > MAX_TOTAL_POINTS

    def test_reports_cost_per_constraint(self, chart, conflicting):
        result = solve(chart, catalog, target(), conflicting)
        assert isinstance(result, Infeasible)
        assert len(result.per_constraint_points) == len(conflicting)
        assert result.impossible == ()

    def test_separates_impossible_from_expensive(self, chart):
        """어떤 투자로도 불가능한 제약과, 비싸서 못 넣는 제약은 다르다.

        둘을 같은 숫자로 뭉개면 "0포인트면 되는데 왜 안 되지?" 가 된다.
        """
        result = solve(
            chart,
            catalog,
            target(),
            [OutspeedConstraint("상한 초과", 999), OutspeedConstraint("가능", 130)],
        )
        assert isinstance(result, Infeasible)
        assert result.impossible == ("상한 초과",)
        assert result.per_constraint_points == {"가능": 9}

    def test_offers_options_the_user_can_choose_from(self, chart, conflicting):
        """③번 — 포기할 조합을 근거와 함께 보여준다."""
        result = solve(chart, catalog, target(), conflicting)
        assert isinstance(result, Infeasible)
        assert len(result.options) > 0
        for option in result.options:
            assert option.spread.total <= MAX_TOTAL_POINTS
            assert len(option.satisfied) > 0
            assert len(option.dropped) > 0

    def test_options_explain_what_is_gained(self, chart, conflicting):
        result = solve(chart, catalog, target(), conflicting)
        assert isinstance(result, Infeasible)
        for option in result.options:
            assert len(option.justifications) > 0

    def test_options_are_maximal(self, chart, conflicting):
        """어떤 선택지도 다른 선택지의 부분집합이면 안 된다."""
        result = solve(chart, catalog, target(), conflicting)
        assert isinstance(result, Infeasible)
        sets = [frozenset(option.satisfied) for option in result.options]
        for a in sets:
            assert not any(a < b for b in sets)

    def test_single_impossible_constraint_has_no_options(self, chart):
        """하나뿐인 제약이 자체로 불가능하면 제시할 대안이 없다."""
        result = solve(chart, catalog, target(), [OutspeedConstraint("불가능", 999)])
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
        result = solve(chart, catalog, target(), three_costly)
        assert isinstance(result, Infeasible)
        satisfied_sets = [frozenset(option.satisfied) for option in result.options]
        assert frozenset({"확정 1타", "얼음 버티기"}) not in satisfied_sets

    def test_feasible_subsets_are_offered(self, chart, three_costly):
        result = solve(chart, catalog, target(), three_costly)
        assert isinstance(result, Infeasible)
        satisfied_sets = [frozenset(option.satisfied) for option in result.options]
        assert frozenset({"추월 130", "확정 1타"}) in satisfied_sets
        assert frozenset({"추월 130", "얼음 버티기"}) in satisfied_sets


class TestResultIsImmutable:
    def test_solution_is_frozen(self, chart):
        result = solve(chart, catalog, target(), [OutspeedConstraint("상대 130", 130)])
        assert isinstance(result, Solution)
        with pytest.raises(AttributeError):
            result.spread = None


class TestNatureValidation:
    """성격 상승보정은 한 스탯에만 걸린다 (2026-11-07 개발자 확인).

    두 스탯이 동시에 1.1배인 포켓몬은 존재할 수 없다. 막지 않으면
    불가능한 배분으로 계산한 수치가 사용자에게 나간다 — 5-0 의 정신과 같다.
    """

    def test_rejects_two_rising_stats(self, chart):
        from app.domain.exceptions import InvalidNatureSpread

        with pytest.raises(InvalidNatureSpread):
            solve(
                chart,
                catalog,
                target(
                    natures={
                        StatKey.ATTACK: NatureModifier.UP,
                        StatKey.SPEED: NatureModifier.UP,
                    }
                ),
                [],
            )

    def test_rejects_two_falling_stats(self, chart):
        from app.domain.exceptions import InvalidNatureSpread

        with pytest.raises(InvalidNatureSpread):
            solve(
                chart,
                catalog,
                target(
                    natures={
                        StatKey.ATTACK: NatureModifier.DOWN,
                        StatKey.SPEED: NatureModifier.DOWN,
                    }
                ),
                [],
            )

    def test_rejects_nature_on_hp(self, chart):
        """HP 는 성격 보정을 받지 않는다 (CLAUDE.md 5-1)."""
        from app.domain.exceptions import InvalidNatureSpread

        with pytest.raises(InvalidNatureSpread):
            solve(chart, catalog, target(natures={StatKey.HP: NatureModifier.UP}), [])

    def test_rejects_rising_without_falling(self):
        """성격은 항상 쌍이다 — 하나를 올리면 반드시 하나를 내린다."""
        from app.domain.exceptions import InvalidNatureSpread

        with pytest.raises(InvalidNatureSpread):
            BuildTarget(base_stats=GARCHOMP, natures={StatKey.SPEED: NatureModifier.UP})

    def test_rejects_falling_without_rising(self):
        from app.domain.exceptions import InvalidNatureSpread

        with pytest.raises(InvalidNatureSpread):
            BuildTarget(base_stats=GARCHOMP, natures={StatKey.SPEED: NatureModifier.DOWN})

    def test_accepts_neutral_nature(self):
        """무보정 성격은 상승도 하락도 없다."""
        assert BuildTarget(base_stats=GARCHOMP, natures={}) is not None

    def test_accepts_one_up_and_one_down(self, chart):
        result = solve(
            chart,
            catalog,
            target(
                natures={
                    StatKey.SPEED: NatureModifier.UP,
                    StatKey.SP_ATTACK: NatureModifier.DOWN,
                }
            ),
            [],
        )
        assert isinstance(result, Solution)


class TestResidualAllocation:
    """규칙 4 — 남은 포인트를 사용자가 지정한 우선순위에 분배한다."""

    def test_no_priority_leaves_points_unspent(self, chart):
        """우선순위를 안 주면 제약 만족에 필요한 만큼만 쓴다."""
        result = solve(chart, catalog, target(), [OutspeedConstraint("상대 130", 130)])
        assert isinstance(result, Solution)
        assert result.spread.total == 9

    def test_spends_the_remaining_budget(self, chart):
        result = solve(
            chart,
            catalog,
            target(),
            [OutspeedConstraint("상대 130", 130)],
            priority=AllocationPriority.PHYSICAL_BULK,
        )
        assert isinstance(result, Solution)
        assert result.spread.total == MAX_TOTAL_POINTS

    def test_physical_bulk_goes_to_hp_and_defense(self, chart):
        result = solve(chart, catalog, target(), [], priority=AllocationPriority.PHYSICAL_BULK)
        assert isinstance(result, Solution)
        assert result.spread.hp > 0
        assert result.spread.defense > 0
        assert result.spread.sp_defense == 0

    def test_special_bulk_goes_to_hp_and_special_defense(self, chart):
        result = solve(chart, catalog, target(), [], priority=AllocationPriority.SPECIAL_BULK)
        assert isinstance(result, Solution)
        assert result.spread.sp_defense > 0
        assert result.spread.defense == 0

    def test_bulk_allocation_maximises_the_index(self, chart):
        """①번 — 실수치 합이 아니라 실제 내구력 공식(HP × 방어)을 최대화한다."""
        from app.domain.indices import bulk_index

        result = solve(chart, catalog, target(), [], priority=AllocationPriority.PHYSICAL_BULK)
        assert isinstance(result, Solution)
        t = target()
        chosen = bulk_index(
            hp_stat=t.actual(StatKey.HP, result.spread.hp),
            defense_stat=t.actual(StatKey.DEFENSE, result.spread.defense),
        )
        for hp_points in range(MAX_POINTS_PER_STAT + 1):
            defense_points = min(MAX_TOTAL_POINTS - hp_points, MAX_POINTS_PER_STAT)
            if defense_points < 0:
                continue
            rival = bulk_index(
                hp_stat=t.actual(StatKey.HP, hp_points),
                defense_stat=t.actual(StatKey.DEFENSE, defense_points),
            )
            assert chosen >= rival

    def test_offense_priority_invests_in_attack(self, chart):
        result = solve(chart, catalog, target(), [], priority=AllocationPriority.PHYSICAL_OFFENSE)
        assert isinstance(result, Solution)
        assert result.spread.attack == MAX_POINTS_PER_STAT

    def test_speed_priority_invests_in_speed(self, chart):
        result = solve(chart, catalog, target(), [], priority=AllocationPriority.SPEED)
        assert isinstance(result, Solution)
        assert result.spread.speed == MAX_POINTS_PER_STAT

    def test_allocation_is_justified(self, chart):
        result = solve(chart, catalog, target(), [], priority=AllocationPriority.PHYSICAL_BULK)
        assert isinstance(result, Solution)
        assert any("물리내구" in j.constraint_label for j in result.justifications)


class TestMagicNumberLanding:
    """매직넘버는 상승보정 스탯 하나에만 적용된다 (성격은 한 스탯만 1.1배)."""

    def test_pushes_one_point_to_land(self, chart):
        """추월에 7포인트면 충분하지만, 1포인트 더 주면 실수치가 2 오른다."""
        rising = target(
            natures={StatKey.SPEED: NatureModifier.UP, StatKey.SP_ATTACK: NatureModifier.DOWN}
        )
        # 상승보정 스피드: p=7 -> 141, p=8 -> 143 (매직넘버)
        result = solve(
            chart,
            catalog,
            rising,
            [OutspeedConstraint("상대 140", 140)],
            priority=AllocationPriority.PHYSICAL_BULK,
        )
        assert isinstance(result, Solution)
        assert result.spread.speed == 8

    def test_landing_is_justified(self, chart):
        rising = target(
            natures={StatKey.SPEED: NatureModifier.UP, StatKey.SP_ATTACK: NatureModifier.DOWN}
        )
        result = solve(
            chart,
            catalog,
            rising,
            [OutspeedConstraint("상대 140", 140)],
            priority=AllocationPriority.PHYSICAL_BULK,
        )
        assert isinstance(result, Solution)
        assert any("매직넘버" in j.detail for j in result.justifications)

    def test_no_landing_without_rising_nature(self, chart):
        """무보정에는 매직넘버가 없다. 최소 포인트 그대로."""
        result = solve(
            chart,
            catalog,
            target(),
            [OutspeedConstraint("상대 130", 130)],
            priority=AllocationPriority.PHYSICAL_BULK,
        )
        assert isinstance(result, Solution)
        assert result.spread.speed == 9

    def test_landing_respects_the_budget(self, chart):
        rising = target(
            natures={StatKey.SPEED: NatureModifier.UP, StatKey.SP_ATTACK: NatureModifier.DOWN}
        )
        result = solve(
            chart,
            catalog,
            rising,
            [OutspeedConstraint("상대 140", 140)],
            priority=AllocationPriority.PHYSICAL_BULK,
        )
        assert isinstance(result, Solution)
        assert result.spread.total <= MAX_TOTAL_POINTS


class TestAllocationEdges:
    """잔여 배분의 경계 — 예산이 없거나 이미 상한인 경우."""

    def test_no_room_to_land_on_magic_number(self, chart):
        """매직넘버까지 거리가 남은 예산보다 크면 착지하지 않는다."""
        rising = target(
            natures={StatKey.SPEED: NatureModifier.UP, StatKey.SP_ATTACK: NatureModifier.DOWN}
        )
        constraints = [
            OutspeedConstraint("추월", 134),  # 상승보정 0포인트가 134 -> 1포인트 필요
            SurviveConstraint(
                "얼음 버티기",
                opponent=opponent_attacker(attack=200, types=(PokemonType.ICE,)),
                move=ice_move(),
            ),
            KnockoutConstraint("확정 1타", opponent=opponent_defender(hp=100), move=physical()),
        ]
        result = solve(chart, catalog, rising, constraints, priority=AllocationPriority.SPEED)
        if isinstance(result, Solution):
            assert result.spread.total <= MAX_TOTAL_POINTS

    def test_budget_exhausted_by_constraints(self, chart):
        """제약이 예산을 다 쓰면 잔여 배분이 아무것도 하지 않는다."""
        constraints = [
            SurviveConstraint(
                "얼음 버티기",
                opponent=opponent_attacker(attack=200, types=(PokemonType.ICE,)),
                move=ice_move(),
            ),
            OutspeedConstraint("추월 150", 150),
        ]
        result = solve(
            chart, catalog, target(), constraints, priority=AllocationPriority.SPECIAL_BULK
        )
        assert isinstance(result, Solution)
        assert result.spread.total <= MAX_TOTAL_POINTS

    def test_priority_stat_already_at_maximum(self, chart):
        """제약이 이미 스탯을 32까지 밀어놨으면 더 못 넣는다."""
        constraints = [
            KnockoutConstraint("확정 1타", opponent=opponent_defender(hp=104), move=physical())
        ]
        result = solve(
            chart, catalog, target(), constraints, priority=AllocationPriority.PHYSICAL_OFFENSE
        )
        assert isinstance(result, Solution)
        assert result.spread.attack == MAX_POINTS_PER_STAT

    def test_magic_landing_consumes_entire_budget(self, chart):
        """매직넘버 착지로 예산이 정확히 0이 되면 그 뒤 배분은 없다."""
        rising = target(
            natures={StatKey.SPEED: NatureModifier.UP, StatKey.SP_ATTACK: NatureModifier.DOWN}
        )
        constraints = [
            SurviveConstraint(
                "얼음 버티기",
                opponent=opponent_attacker(attack=200, types=(PokemonType.ICE,)),
                move=ice_move(),
            ),
            OutspeedConstraint("추월 160", 160),
        ]
        result = solve(chart, catalog, rising, constraints, priority=AllocationPriority.SPEED)
        if isinstance(result, Solution):
            assert result.spread.total <= MAX_TOTAL_POINTS

    def test_constraints_consume_the_entire_budget(self, chart):
        """제약이 66 을 정확히 다 쓰면 우선순위를 줘도 더 넣을 게 없다."""
        constraints = [
            SurviveConstraint(
                "얼음 버티기",
                opponent=opponent_attacker(attack=200, types=(PokemonType.ICE,)),
                move=ice_move(),
            ),
            OutspeedConstraint("추월 151", 151),
        ]
        without = solve(chart, catalog, target(), constraints)
        assert isinstance(without, Solution)
        assert without.spread.total == MAX_TOTAL_POINTS

        with_priority = solve(
            chart, catalog, target(), constraints, priority=AllocationPriority.PHYSICAL_BULK
        )
        assert isinstance(with_priority, Solution)
        assert with_priority.spread == without.spread


class TestBattleContext:
    """날씨·급소·벽·필드가 제약 판정에 반영된다 (B 작업)."""

    def test_sun_boosts_fire_knockout(self, chart, catalog):
        """쾌청이면 불꽃 기술이 1.5배라 더 적은 포인트로 잡는다."""
        # 자속이 없는 불꽃 기술. 평상시 7포인트, 쾌청이면 0포인트로 잡는다.
        constraint = KnockoutConstraint(
            "상대",
            opponent=opponent_defender(hp=60),
            move=MoveSpec(power=100, move_type=PokemonType.FIRE, category=MoveCategory.PHYSICAL),
        )
        clear = solve(chart, catalog, target(), [constraint])
        sunny = solve(
            chart,
            catalog,
            target(),
            [constraint],
            context=BattleContext(weather=Weather.SUN),
        )
        assert isinstance(sunny, Solution)
        if isinstance(clear, Solution):
            assert sunny.spread.attack < clear.spread.attack

    def test_screen_helps_surviving(self, chart, catalog):
        """벽이 깔려 있으면 더 적은 내구 투자로 버틴다."""
        constraint = SurviveConstraint(
            "얼음",
            opponent=opponent_attacker(attack=200, types=(PokemonType.ICE,)),
            move=ice_move(),
        )
        bare = solve(chart, catalog, target(), [constraint])
        walled = solve(
            chart,
            catalog,
            target(),
            [constraint],
            context=BattleContext(screen=0.5),
        )
        assert isinstance(bare, Solution)
        assert isinstance(walled, Solution)
        assert walled.spread.total < bare.spread.total

    def test_critical_hit_makes_surviving_harder(self, chart, catalog):
        constraint = SurviveConstraint(
            "얼음 급소",
            opponent=opponent_attacker(attack=200, types=(PokemonType.ICE,)),
            move=ice_move(),
        )
        normal = solve(chart, catalog, target(), [constraint])
        crit = solve(
            chart,
            catalog,
            target(),
            [constraint],
            context=BattleContext(is_critical=True),
        )
        assert isinstance(normal, Solution)
        if isinstance(crit, Solution):
            assert crit.spread.total > normal.spread.total

    def test_context_is_reported_in_assumptions_free_form(self, chart, catalog):
        """맥락이 결과를 바꿨다는 사실은 근거 문장에 드러나야 한다."""
        result = solve(
            chart,
            catalog,
            target(),
            [
                SurviveConstraint(
                    "얼음",
                    opponent=opponent_attacker(attack=200, types=(PokemonType.ICE,)),
                    move=ice_move(),
                )
            ],
            context=BattleContext(screen=0.5),
        )
        assert isinstance(result, Solution)
        assert result.justifications


class TestItemWiring:
    """도구 효과가 제약 판정에 반영된다 — items.py 가 설계대로 쓰인다."""

    def test_life_orb_reduces_required_attack(self, chart, catalog):
        constraint = KnockoutConstraint(
            "HP100 상대", opponent=opponent_defender(hp=100), move=physical()
        )
        bare = solve(chart, catalog, target(), [constraint])
        orb = solve(chart, catalog, target(item_id="lifeorb"), [constraint])
        assert isinstance(bare, Solution)
        assert isinstance(orb, Solution)
        assert orb.spread.attack < bare.spread.attack

    def test_choice_scarf_reduces_required_speed(self, chart, catalog):
        constraint = OutspeedConstraint("상대 160", 160)
        bare = solve(chart, catalog, target(), [constraint])
        scarf = solve(chart, catalog, target(item_id="choicescarf"), [constraint])
        assert isinstance(scarf, Solution)
        if isinstance(bare, Solution):
            assert scarf.spread.speed < bare.spread.speed

    def test_non_damage_item_changes_nothing(self, chart, catalog):
        """먹다남은음식은 데미지 계산과 무관하다 (kind: NONE)."""
        constraint = KnockoutConstraint(
            "HP100 상대", opponent=opponent_defender(hp=100), move=physical()
        )
        bare = solve(chart, catalog, target(), [constraint])
        leftovers = solve(chart, catalog, target(item_id="leftovers"), [constraint])
        assert isinstance(bare, Solution)
        assert isinstance(leftovers, Solution)
        assert leftovers.spread == bare.spread

    def test_opponent_shuca_berry_makes_knockout_harder(self, chart, catalog):
        """슈캐열매는 효과굉장 땅 기술을 반감한다."""
        with_berry = solve(
            chart,
            catalog,
            target(),
            [
                KnockoutConstraint(
                    "전기 타입 상대",
                    opponent=opponent_defender(hp=100, types=(PokemonType.ELECTRIC,)),
                    move=physical(),
                    opponent_item_id="shucaberry",
                )
            ],
        )
        without = solve(
            chart,
            catalog,
            target(),
            [
                KnockoutConstraint(
                    "전기 타입 상대",
                    opponent=opponent_defender(hp=100, types=(PokemonType.ELECTRIC,)),
                    move=physical(),
                )
            ],
        )
        assert isinstance(without, Solution)
        if isinstance(with_berry, Solution):
            assert with_berry.spread.attack > without.spread.attack

    def test_air_balloon_makes_ground_knockout_impossible(self, chart, catalog):
        """풍선은 땅 기술을 무효화한다. 어떤 투자로도 잡을 수 없다."""
        result = solve(
            chart,
            catalog,
            target(),
            [
                KnockoutConstraint(
                    "풍선 상대",
                    opponent=opponent_defender(hp=100),
                    move=physical(),
                    opponent_item_id="airballoon",
                )
            ],
        )
        assert isinstance(result, Infeasible)
        assert result.impossible == ("풍선 상대",)

    def test_our_air_balloon_makes_surviving_free(self, chart, catalog):
        """우리가 풍선을 들면 땅 기술을 0포인트로 버틴다."""
        result = solve(
            chart,
            catalog,
            target(item_id="airballoon"),
            [
                SurviveConstraint(
                    "강한 지진",
                    opponent=opponent_attacker(attack=255, types=(PokemonType.GROUND,)),
                    move=physical(power=150, move_type=PokemonType.GROUND),
                )
            ],
        )
        assert isinstance(result, Solution)
        assert result.spread.total == 0

    def test_unknown_item_fails_closed(self, chart, catalog):
        """모르는 도구는 무시하지 않고 실패시킨다 (ADR-005 의 원칙)."""
        from app.domain.exceptions import UnknownItemEffect

        with pytest.raises(UnknownItemEffect):
            solve(chart, catalog, target(item_id="mystery"), [])


class TestRankStageWiring:
    """랭크업이 반영된다. 용의춤 쌓은 상대를 버티는 계산이 가능해야 한다."""

    def test_opponent_rank_up_makes_surviving_harder(self, chart, catalog):
        plain = solve(
            chart,
            catalog,
            target(),
            [
                SurviveConstraint(
                    "얼음",
                    opponent=opponent_attacker(attack=200, types=(PokemonType.ICE,)),
                    move=ice_move(),
                )
            ],
        )
        hard = solve(
            chart,
            catalog,
            target(),
            [
                SurviveConstraint(
                    "용의춤 1번 쌓은 얼음",
                    opponent=opponent_attacker(attack=200, types=(PokemonType.ICE,)),
                    move=ice_move(),
                    opponent_rank_stage=1,
                )
            ],
        )
        assert isinstance(plain, Solution)
        if isinstance(hard, Solution):
            assert hard.spread.total > plain.spread.total

    def test_our_rank_up_reduces_required_attack(self, chart, catalog):
        constraint = KnockoutConstraint(
            "HP100 상대", opponent=opponent_defender(hp=100), move=physical()
        )
        bare = solve(chart, catalog, target(), [constraint])
        boosted = solve(chart, catalog, target(rank_stages={StatKey.ATTACK: 1}), [constraint])
        assert isinstance(bare, Solution)
        assert isinstance(boosted, Solution)
        assert boosted.spread.attack < bare.spread.attack

    def test_rank_up_applies_to_speed(self, chart, catalog):
        constraint = OutspeedConstraint("상대 160", 160)
        bare = solve(chart, catalog, target(), [constraint])
        boosted = solve(chart, catalog, target(rank_stages={StatKey.SPEED: 1}), [constraint])
        assert isinstance(boosted, Solution)
        if isinstance(bare, Solution):
            assert boosted.spread.speed < bare.spread.speed

    def test_rank_down_makes_things_harder(self, chart, catalog):
        constraint = OutspeedConstraint("상대 130", 130)
        bare = solve(chart, catalog, target(), [constraint])
        lowered = solve(chart, catalog, target(rank_stages={StatKey.SPEED: -1}), [constraint])
        assert isinstance(bare, Solution)
        if isinstance(lowered, Solution):
            assert lowered.spread.speed > bare.spread.speed

    def test_out_of_range_stage_is_rejected(self, chart, catalog):
        from app.domain.exceptions import InvalidRankStage

        with pytest.raises(InvalidRankStage):
            solve(chart, catalog, target(rank_stages={StatKey.ATTACK: 7}), [])

    def test_hp_rank_stage_is_rejected(self, chart, catalog):
        """HP 에는 랭크가 없다."""
        from app.domain.exceptions import InvalidRankStage

        with pytest.raises(InvalidRankStage):
            solve(chart, catalog, target(rank_stages={StatKey.HP: 1}), [])

    def test_opponent_defense_rank_up_makes_knockout_harder(self, chart, catalog):
        """상대가 방어를 올리면 더 많은 공격 투자가 필요하다."""
        plain = solve(
            chart,
            catalog,
            target(),
            [KnockoutConstraint("HP100 상대", opponent=opponent_defender(hp=100), move=physical())],
        )
        hardened = solve(
            chart,
            catalog,
            target(),
            [
                KnockoutConstraint(
                    "철벽 쌓은 상대",
                    opponent=opponent_defender(hp=100),
                    move=physical(),
                    opponent_rank_stage=1,
                )
            ],
        )
        assert isinstance(plain, Solution)
        if isinstance(hardened, Solution):
            assert hardened.spread.attack > plain.spread.attack
        else:
            assert isinstance(hardened, Infeasible)

    def test_opponent_rank_stage_out_of_range_is_rejected(self, chart, catalog):
        """상대 랭크도 범위를 벗어나면 막는다. 받는 지점에서 검증한다."""
        from app.domain.exceptions import InvalidRankStage

        with pytest.raises(InvalidRankStage):
            solve(
                chart,
                catalog,
                target(),
                [OutspeedConstraint("상대", 120, opponent_rank_stage=7)],
            )
