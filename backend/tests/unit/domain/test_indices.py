"""결정력 / 내구력 지표 테스트 (CLAUDE.md 5-3).

**표시 전용 근사 지표다.** 확정 n타 판정에는 절대 쓰지 않는다 —
그건 damage.py 의 16 roll 이 한다.

여기 테스트의 역할은 "정확한가"가 아니라 **"얼마나 부정확한지를 고정하는 것"** 이다.
오차 한계가 테스트로 박혀 있어야 UI 가 어느 정도 신뢰도로 표시할지 정할 수 있다.
"""

import json
import random
import statistics
from pathlib import Path

import pytest

from app.domain.damage import calculate
from app.domain.indices import (
    BULK_CONSTANT,
    bulk_index,
    estimated_damage_ratio,
    offense_index,
)
from app.domain.models import Attacker, Defender, MoveCategory, MoveSpec
from app.domain.typechart import PokemonType, TypeChart, effectiveness

TYPECHART_PATH = Path(__file__).resolve().parents[3] / "data" / "typechart.json"


@pytest.fixture(scope="module")
def chart():
    with TYPECHART_PATH.open(encoding="utf-8") as file:
        return TypeChart.from_mapping(json.load(file)["chart"])


class TestOffenseIndex:
    def test_formula(self):
        # 공격실수치 × 기술위력 × 자속 × 기타
        assert offense_index(attack_stat=150, power=100) == 15000
        assert offense_index(attack_stat=150, power=100, stab=1.5) == 22500

    def test_other_modifiers_multiply(self):
        assert offense_index(attack_stat=100, power=100, stab=1.5, other=1.3) == pytest.approx(
            100 * 100 * 1.5 * 1.3
        )

    def test_scales_with_attack(self):
        low = offense_index(attack_stat=100, power=100)
        high = offense_index(attack_stat=200, power=100)
        assert high == 2 * low

    def test_rejects_invalid_input(self):
        with pytest.raises(ValueError):
            offense_index(attack_stat=0, power=100)
        with pytest.raises(ValueError):
            offense_index(attack_stat=100, power=-1)


class TestBulkIndex:
    def test_formula(self):
        # HP실수치 × 방어실수치 / K
        assert bulk_index(hp_stat=200, defense_stat=150) == pytest.approx(200 * 150 / BULK_CONSTANT)

    def test_scales_with_both_axes(self):
        base = bulk_index(hp_stat=200, defense_stat=150)
        assert bulk_index(hp_stat=400, defense_stat=150) == pytest.approx(2 * base)
        assert bulk_index(hp_stat=200, defense_stat=300) == pytest.approx(2 * base)

    def test_ranking_is_independent_of_the_constant(self):
        """K 는 상수라 순위를 바꾸지 않는다.

        "누가 더 단단한가"만 볼 거라면 K 값은 아무 상관이 없다.
        K 가 의미를 갖는 건 평균 데미지% 를 추정할 때뿐이다.
        """
        pairs = [(200, 150), (180, 200), (260, 100)]
        with_default = [bulk_index(hp_stat=hp, defense_stat=d) for hp, d in pairs]
        with_other = [bulk_index(hp_stat=hp, defense_stat=d, k=0.5) for hp, d in pairs]
        assert [sorted(with_default).index(v) for v in with_default] == [
            sorted(with_other).index(v) for v in with_other
        ]

    def test_rejects_invalid_input(self):
        with pytest.raises(ValueError):
            bulk_index(hp_stat=0, defense_stat=100)
        with pytest.raises(ValueError):
            bulk_index(hp_stat=100, defense_stat=0)

    def test_rejects_non_positive_constant(self):
        """K 로 나누므로 0 이하면 0 나눗셈이거나 부호가 뒤집힌다."""
        with pytest.raises(ValueError):
            bulk_index(hp_stat=200, defense_stat=150, k=0)
        with pytest.raises(ValueError):
            bulk_index(hp_stat=200, defense_stat=150, k=-0.4)


class TestCalibration:
    """5-3: K 는 정확한 데미지 계산 결과와 대조해 캘리브레이션한다."""

    def test_constant_is_the_measured_value(self):
        """0.417 은 damage.py 와 대조해 얻은 값이다. 커뮤니티 통용값 0.411 계열."""
        assert pytest.approx(0.417) == BULK_CONSTANT

    def test_stays_close_to_the_exact_engine(self, chart):
        """근사 오차를 고정한다. 느슨해지면 UI 신뢰도 표기를 다시 정해야 한다."""
        random.seed(42)
        errors = []
        types = list(PokemonType)
        for _ in range(500):
            attack = random.randint(80, 220)
            defense = random.randint(80, 220)
            hp = random.randint(120, 260)
            power = random.choice([40, 60, 80, 100, 120])
            move_type = random.choice(types)
            attacker_type = random.choice(types)
            defender_type = random.choice(types)

            eff = effectiveness(chart, move_type, (defender_type,))
            if eff == 0:
                continue

            result = calculate(
                chart,
                Attacker(attack_stat=attack, special_attack_stat=attack, types=(attacker_type,)),
                Defender(
                    defense_stat=defense,
                    special_defense_stat=defense,
                    hp_stat=hp,
                    types=(defender_type,),
                ),
                MoveSpec(power=power, move_type=move_type, category=MoveCategory.PHYSICAL),
            )
            exact = statistics.mean(result.rolls) / hp

            stab = 1.5 if move_type is attacker_type else 1.0
            estimate = estimated_damage_ratio(
                offense_index(attack_stat=attack, power=power, stab=stab),
                bulk_index(hp_stat=hp, defense_stat=defense),
                type_effectiveness=eff,
            )
            errors.append(abs(estimate - exact) / exact * 100)

        assert statistics.mean(errors) < 2.0, f"평균 오차 {statistics.mean(errors):.2f}%"
        assert statistics.median(errors) < 1.5
        assert max(errors) < 20.0, f"최대 오차 {max(errors):.2f}%"

    def test_is_an_approximation_not_an_oracle(self, chart):
        """근사라는 사실 자체를 고정한다. 정확히 일치하면 오히려 의심해야 한다."""
        result = calculate(
            chart,
            Attacker(attack_stat=150, special_attack_stat=150, types=(PokemonType.GROUND,)),
            Defender(
                defense_stat=120,
                special_defense_stat=120,
                hp_stat=200,
                types=(PokemonType.NORMAL,),
            ),
            MoveSpec(power=100, move_type=PokemonType.GROUND, category=MoveCategory.PHYSICAL),
        )
        exact = statistics.mean(result.rolls) / 200
        estimate = estimated_damage_ratio(
            offense_index(attack_stat=150, power=100, stab=1.5),
            bulk_index(hp_stat=200, defense_stat=120),
            type_effectiveness=1.0,
        )
        assert estimate != exact


class TestEstimatedDamage:
    def test_type_effectiveness_scales_the_estimate(self):
        offense = offense_index(attack_stat=150, power=100)
        bulk = bulk_index(hp_stat=200, defense_stat=120)
        neutral = estimated_damage_ratio(offense, bulk, type_effectiveness=1.0)
        doubled = estimated_damage_ratio(offense, bulk, type_effectiveness=2.0)
        assert doubled == pytest.approx(2 * neutral)

    def test_immunity_gives_zero(self):
        offense = offense_index(attack_stat=150, power=100)
        bulk = bulk_index(hp_stat=200, defense_stat=120)
        assert estimated_damage_ratio(offense, bulk, type_effectiveness=0.0) == 0.0

    def test_rejects_zero_bulk(self):
        with pytest.raises(ValueError):
            estimated_damage_ratio(1000.0, 0.0, type_effectiveness=1.0)
