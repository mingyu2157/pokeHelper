"""데미지 계산 테스트.

보정 체인은 ADR-003 의 A안이다 — 보정을 실수로 누적하고 맨 마지막에 한 번만 내린다.
A/B 가 갈리는 경계 케이스를 테스트로 고정해, 누군가 B안으로 바꾸면 즉시 깨지게 한다.
"""

import json
from pathlib import Path

import pytest

from app.domain.damage import (
    DamageResult,
    Modifiers,
    Weather,
    base_damage,
    calculate,
)
from app.domain.models import Attacker, Defender, MoveCategory, MoveSpec
from app.domain.typechart import PokemonType, TypeChart

TYPECHART_PATH = Path(__file__).resolve().parents[3] / "data" / "typechart.json"


@pytest.fixture(scope="module")
def chart():
    with TYPECHART_PATH.open(encoding="utf-8") as file:
        return TypeChart.from_mapping(json.load(file)["chart"])


def move(power=100, move_type=PokemonType.NORMAL, category=MoveCategory.PHYSICAL):
    return MoveSpec(power=power, move_type=move_type, category=category)


def attacker(attack=100, types=(PokemonType.NORMAL,), is_burned=False):
    return Attacker(
        attack_stat=attack, special_attack_stat=attack, types=types, is_burned=is_burned
    )


def defender(defense=100, hp=200, types=(PokemonType.NORMAL,)):
    return Defender(defense_stat=defense, special_defense_stat=defense, hp_stat=hp, types=types)


class TestBaseDamage:
    """기초 = floor(22 × 위력 × 공격 / 방어), floor(기초 / 50) + 2"""

    def test_formula(self):
        # floor(22 * 100 * 100 / 100) = 2200 -> floor(2200/50) + 2 = 44 + 2 = 46
        assert base_damage(power=100, attack=100, defense=100) == 46

    @pytest.mark.parametrize(
        ("power", "attack", "defense", "expected"),
        [
            (100, 100, 100, 46),
            (100, 200, 100, 90),  # floor(22*100*200/100)=4400 -> 88+2
            (60, 150, 120, 35),  # floor(22*60*150/120)=1650 -> 33+2
            (1, 1, 255, 2),  # 최소. +2 때문에 2 밑으로 내려가지 않는다
        ],
    )
    def test_cases(self, power, attack, defense, expected):
        assert base_damage(power=power, attack=attack, defense=defense) == expected

    def test_floor_is_applied_twice(self):
        """나누기마다 내림한다. 한 번에 계산하면 값이 어긋난다."""
        # 22*55*101/99 = 1246.06... -> floor 1246 -> floor(1246/50)=24 -> 26
        assert base_damage(power=55, attack=101, defense=99) == 26

    def test_zero_defense_is_rejected(self):
        with pytest.raises(ValueError):
            base_damage(power=100, attack=100, defense=0)

    def test_zero_attack_is_rejected(self):
        with pytest.raises(ValueError):
            base_damage(power=100, attack=0, defense=100)

    def test_negative_power_is_rejected(self):
        with pytest.raises(ValueError):
            base_damage(power=-1, attack=100, defense=100)


class TestModifierChainIsOptionA:
    """ADR-003 A안 고정. B안으로 바꾸면 이 테스트가 깨져야 한다."""

    def test_boundary_case_from_adr(self):
        """CLAUDE.md 5-2 의 예시: 기초 100, 난수 0.85, 자속 1.5, 약점 2배.

        A안: floor(100 × 3.0 × 0.85) = 255
        B안: floor(100×0.85)=85 -> floor(85×1.5)=127 -> ×2 = 254
        """
        mods = Modifiers(stab=1.5, type_effectiveness=2.0)
        rolls = mods.apply(100)
        assert rolls[0] == 255, "최소 roll 이 254 면 B안으로 바뀐 것이다"

    def test_modifier_order_does_not_matter(self):
        """A안에서는 보정 적용 순서가 결과에 영향을 주지 않는다."""
        a = Modifiers(stab=1.5, type_effectiveness=2.0, crit=1.5).apply(100)
        b = Modifiers(crit=1.5, type_effectiveness=2.0, stab=1.5).apply(100)
        assert a == b

    def test_sixteen_rolls(self):
        rolls = Modifiers().apply(100)
        assert len(rolls) == 16
        assert rolls[0] == 85  # floor(100 * 0.85)
        assert rolls[-1] == 100  # floor(100 * 1.00)

    def test_rolls_are_non_decreasing(self):
        rolls = Modifiers(stab=1.5, type_effectiveness=2.0).apply(123)
        assert list(rolls) == sorted(rolls)

    def test_zero_multiplier_zeroes_every_roll(self):
        rolls = Modifiers(type_effectiveness=0.0).apply(100)
        assert set(rolls) == {0}


class TestMinimumDamageIsOne:
    """데미지가 0으로 떨어지면 1로 올린다 (본가와 동일, 2026-11-05 개발자 확인).

    단 **무효(타입 상성 0배)일 때는 0을 유지한다.** 맞지 않는 것과
    아주 조금 맞는 것은 다르다.
    """

    def test_floor_to_zero_becomes_one(self):
        # 기초 2 x 0.25(2타입 저항) x 0.85 = 0.425 -> floor 0 -> 1 로 올림
        rolls = Modifiers(type_effectiveness=0.25).apply(2)
        assert rolls[0] == 1
        assert min(rolls) >= 1

    def test_immunity_stays_zero(self):
        rolls = Modifiers(type_effectiveness=0.0).apply(100)
        assert set(rolls) == {0}

    def test_zero_from_non_type_modifier_still_becomes_one(self):
        """상성이 아닌 보정이 0이어도, 무효가 아니므로 1로 올린다."""
        rolls = Modifiers(screen=0.0).apply(100)
        assert set(rolls) == {1}

    def test_normal_damage_is_unaffected(self):
        rolls = Modifiers().apply(100)
        assert rolls[0] == 85

    def test_minimum_one_through_calculate(self, chart):
        """약한 공격이 2타입 저항을 맞아도 0이 아니라 1 이상."""
        result = calculate(
            chart,
            attacker(attack=1, types=(PokemonType.GRASS,)),
            defender(defense=255, hp=200, types=(PokemonType.FIRE, PokemonType.FLYING)),
            move(power=1, move_type=PokemonType.GRASS),
        )
        assert min(result.rolls) >= 1

    def test_immune_through_calculate_is_zero(self, chart):
        result = calculate(
            chart,
            attacker(),
            defender(types=(PokemonType.GHOST,)),
            move(move_type=PokemonType.NORMAL),
        )
        assert result.rolls == (0,) * 16


class TestAppliedModifiersExposesEverything:
    """①번 결정 B안 — 1배인 보정도 전부 노출한다.

    "해당 없음"과 "계산을 빠뜨림"을 구분할 수 있어야 한다.
    """

    def test_all_slots_present_even_when_neutral(self, chart):
        result = calculate(
            chart,
            attacker(),
            defender(),
            move(),
        )
        assert set(result.applied_modifiers) == {
            "stab",
            "type_effectiveness",
            "weather",
            "crit",
            "screen",
            "field",
            "ability",
            "item",
        }

    def test_neutral_values_are_one(self, chart):
        result = calculate(chart, attacker(), defender(), move())
        assert result.applied_modifiers["weather"] == 1.0
        assert result.applied_modifiers["crit"] == 1.0
        assert result.applied_modifiers["item"] == 1.0

    def test_caller_supplied_values_are_reported(self, chart):
        result = calculate(
            chart,
            attacker(),
            defender(),
            move(),
            screen=0.5,
            item=1.3,
        )
        assert result.applied_modifiers["screen"] == 0.5
        assert result.applied_modifiers["item"] == 1.3


class TestStab:
    def test_stab_applies_when_types_match(self, chart):
        with_stab = calculate(
            chart, attacker(types=(PokemonType.FIRE,)), defender(), move(move_type=PokemonType.FIRE)
        )
        assert with_stab.applied_modifiers["stab"] == 1.5

    def test_no_stab_when_types_differ(self, chart):
        result = calculate(
            chart,
            attacker(types=(PokemonType.WATER,)),
            defender(),
            move(move_type=PokemonType.FIRE),
        )
        assert result.applied_modifiers["stab"] == 1.0

    def test_stab_on_second_type(self, chart):
        result = calculate(
            chart,
            attacker(types=(PokemonType.WATER, PokemonType.FIRE)),
            defender(),
            move(move_type=PokemonType.FIRE),
        )
        assert result.applied_modifiers["stab"] == 1.5


class TestTypeEffectiveness:
    def test_super_effective(self, chart):
        result = calculate(
            chart,
            attacker(),
            defender(types=(PokemonType.FIRE,)),
            move(move_type=PokemonType.WATER),
        )
        assert result.applied_modifiers["type_effectiveness"] == 2.0

    def test_quad_effective(self, chart):
        result = calculate(
            chart,
            attacker(),
            defender(types=(PokemonType.FIRE, PokemonType.FLYING)),
            move(move_type=PokemonType.ROCK),
        )
        assert result.applied_modifiers["type_effectiveness"] == 4.0

    def test_immunity_produces_zero_damage(self, chart):
        result = calculate(
            chart,
            attacker(),
            defender(types=(PokemonType.GHOST,)),
            move(move_type=PokemonType.NORMAL),
        )
        assert result.rolls == (0,) * 16
        assert result.ohko_chance == 0.0
        assert result.ko_summary == "효과 없음"


class TestWeather:
    @pytest.mark.parametrize(
        ("weather", "move_type", "expected"),
        [
            (Weather.SUN, PokemonType.FIRE, 1.5),
            (Weather.SUN, PokemonType.WATER, 0.5),
            (Weather.RAIN, PokemonType.WATER, 1.5),
            (Weather.RAIN, PokemonType.FIRE, 0.5),
            (Weather.SUN, PokemonType.GRASS, 1.0),
            (Weather.CLEAR, PokemonType.FIRE, 1.0),
            # 모래바람·설경은 데미지 배율이 아니라 스탯 단계에 작용한다 (미구현)
            (Weather.SAND, PokemonType.ROCK, 1.0),
            (Weather.SNOW, PokemonType.ICE, 1.0),
        ],
    )
    def test_weather_multiplier(self, chart, weather, move_type, expected):
        result = calculate(
            chart, attacker(), defender(), move(move_type=move_type), weather=weather
        )
        assert result.applied_modifiers["weather"] == expected


class TestCrit:
    def test_crit_is_one_point_five(self, chart):
        result = calculate(chart, attacker(), defender(), move(), is_critical=True)
        assert result.applied_modifiers["crit"] == 1.5

    def test_crit_increases_damage(self, chart):
        normal = calculate(chart, attacker(), defender(), move())
        crit = calculate(chart, attacker(), defender(), move(), is_critical=True)
        assert crit.rolls[0] > normal.rolls[0]


class TestBurnIsStatStage:
    """화상은 데미지 배율이 아니라 공격 실수치를 반감한다 (champions-helper).

    기초 계산 전에 적용되므로 floor 가 한 번 더 끼어든다.
    데미지 단계에서 0.5 를 곱하는 것과 경계에서 값이 다르다.
    """

    def test_burn_halves_physical_attack(self, chart):
        healthy = calculate(chart, attacker(attack=100), defender(), move())
        burned = calculate(chart, attacker(attack=100, is_burned=True), defender(), move())
        # 공격 100 -> 50 으로 반감된 뒤 기초 계산
        assert burned.rolls == calculate(chart, attacker(attack=50), defender(), move()).rolls
        assert burned.rolls[0] < healthy.rolls[0]

    def test_burn_does_not_affect_special_moves(self, chart):
        special = move(category=MoveCategory.SPECIAL)
        healthy = calculate(chart, attacker(attack=100), defender(), special)
        burned = calculate(chart, attacker(attack=100, is_burned=True), defender(), special)
        assert burned.rolls == healthy.rolls

    def test_burn_is_not_in_applied_modifiers(self, chart):
        """배율이 아니므로 보정 목록에 없다."""
        result = calculate(chart, attacker(is_burned=True), defender(), move())
        assert "burn" not in result.applied_modifiers


class TestPhysicalSpecialSplit:
    def test_special_move_uses_special_stats(self, chart):
        atk = Attacker(attack_stat=50, special_attack_stat=150, types=(PokemonType.NORMAL,))
        dfn = Defender(
            defense_stat=200, special_defense_stat=100, hp_stat=200, types=(PokemonType.NORMAL,)
        )
        physical = calculate(chart, atk, dfn, move(category=MoveCategory.PHYSICAL))
        special = calculate(chart, atk, dfn, move(category=MoveCategory.SPECIAL))
        assert special.rolls[0] > physical.rolls[0]

    def test_status_move_deals_no_damage(self, chart):
        result = calculate(chart, attacker(), defender(), move(category=MoveCategory.STATUS))
        assert result.rolls == (0,) * 16
        assert result.ko_summary == "변화 기술"


class TestKoJudgement:
    """16 roll 중 상대 HP 이상인 개수로 판정한다."""

    def test_guaranteed_ohko(self, chart):
        result = calculate(chart, attacker(attack=255), defender(defense=50, hp=40), move())
        assert result.ohko_chance == 1.0
        assert result.hits_to_ko_min == 1
        assert result.hits_to_ko_max == 1
        assert result.ko_summary == "확정 1타"

    def test_chance_ohko_reports_probability(self, chart):
        result = calculate(chart, attacker(), defender(), move())
        rolls = result.rolls
        hp = next(
            h for h in range(rolls[0] + 1, rolls[-1] + 1) if 0 < sum(r >= h for r in rolls) < 16
        )
        result = calculate(chart, attacker(), defender(hp=hp), move())
        count = sum(r >= hp for r in result.rolls)
        assert result.ohko_chance == pytest.approx(count / 16)
        assert "난수 1타" in result.ko_summary
        assert result.hits_to_ko_min == 1
        assert result.hits_to_ko_max == 2

    def test_guaranteed_two_hit(self, chart):
        result = calculate(chart, attacker(attack=100), defender(defense=100, hp=80), move())
        assert result.ohko_chance == 0.0
        assert result.hits_to_ko_min == 2
        assert result.hits_to_ko_max == 2
        assert result.ko_summary == "확정 2타"

    def test_ohko_chance_is_zero_when_no_roll_reaches_hp(self, chart):
        result = calculate(chart, attacker(), defender(hp=9999), move())
        assert result.ohko_chance == 0.0


class TestPercentages:
    def test_percent_uses_defender_hp(self, chart):
        result = calculate(chart, attacker(), defender(hp=200), move())
        assert result.min_percent == pytest.approx(result.rolls[0] / 200 * 100)
        assert result.max_percent == pytest.approx(result.rolls[-1] / 200 * 100)

    def test_min_is_not_greater_than_max(self, chart):
        result = calculate(chart, attacker(), defender(), move())
        assert result.min_percent <= result.max_percent


class TestResultIsImmutable:
    def test_frozen(self, chart):
        result = calculate(chart, attacker(), defender(), move())
        assert isinstance(result, DamageResult)
        with pytest.raises(AttributeError):
            result.ohko_chance = 1.0

    def test_rolls_is_a_tuple(self, chart):
        result = calculate(chart, attacker(), defender(), move())
        assert isinstance(result.rolls, tuple)


class TestInputValidation:
    def test_negative_power_is_rejected(self, chart):
        with pytest.raises(ValueError):
            calculate(chart, attacker(), defender(), move(power=-1))

    def test_zero_hp_defender_is_rejected(self, chart):
        with pytest.raises(ValueError):
            calculate(chart, attacker(), defender(hp=0), move())
