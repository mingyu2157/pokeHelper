"""타입 상성 테스트.

데이터는 `backend/data/typechart.json` 에서 읽는다 (PokéAPI 생성).
domain 계층은 파일을 읽지 않으므로, 테스트가 JSON 을 읽어 TypeChart 로 주입한다.
"""

import json
from pathlib import Path

import pytest

from app.domain.exceptions import UnknownPokemonType
from app.domain.typechart import PokemonType, TypeChart, effectiveness

TYPECHART_PATH = Path(__file__).resolve().parents[3] / "data" / "typechart.json"


@pytest.fixture(scope="module")
def raw_data():
    with TYPECHART_PATH.open(encoding="utf-8") as file:
        return json.load(file)


@pytest.fixture(scope="module")
def chart(raw_data):
    return TypeChart.from_mapping(raw_data["chart"])


class TestDataFile:
    """생성된 데이터 자체의 불변식. 재생성 시 깨지면 출처가 변한 것이다."""

    def test_eighteen_types(self, raw_data):
        assert len(raw_data["types"]) == 18
        assert len(set(raw_data["types"])) == 18

    def test_champions_has_fairy(self, raw_data):
        """챔피언스는 본가 9세대와 같은 18타입 체계다 (API 전수 조사로 확인)."""
        assert "fairy" in raw_data["types"]

    def test_immunity_count_is_eight(self, raw_data):
        zeros = [
            (atk, dfd)
            for atk, row in raw_data["chart"].items()
            for dfd, mult in row.items()
            if mult == 0.0
        ]
        assert len(zeros) == 8

    def test_all_immunities_are_known(self, raw_data):
        zeros = {
            (atk, dfd)
            for atk, row in raw_data["chart"].items()
            for dfd, mult in row.items()
            if mult == 0.0
        }
        assert zeros == {
            ("normal", "ghost"),
            ("ghost", "normal"),
            ("poison", "steel"),
            ("electric", "ground"),
            ("ground", "flying"),
            ("fighting", "ghost"),
            ("dragon", "fairy"),
            ("psychic", "dark"),
        }

    def test_only_valid_multipliers_are_stored(self, raw_data):
        """표에는 0 / 0.5 / 2 만 들어간다. 1배는 기록하지 않는다."""
        values = {mult for row in raw_data["chart"].values() for mult in row.values()}
        assert values == {0.0, 0.5, 2.0}

    def test_chart_covers_every_type_as_attacker(self, raw_data):
        assert set(raw_data["chart"]) == set(raw_data["types"])


class TestSingleTypeDefender:
    @pytest.mark.parametrize(
        ("attacking", "defending", "expected"),
        [
            # 무효
            ("normal", "ghost", 0.0),
            ("ghost", "normal", 0.0),
            ("electric", "ground", 0.0),
            ("dragon", "fairy", 0.0),
            # 약점
            ("water", "fire", 2.0),
            ("ice", "dragon", 2.0),
            ("steel", "fairy", 2.0),
            ("ghost", "ghost", 2.0),
            # 저항
            ("fire", "water", 0.5),
            ("fairy", "steel", 0.5),
            ("bug", "fairy", 0.5),
            # 중립 (표에 없는 관계)
            ("normal", "water", 1.0),
            ("water", "electric", 1.0),
        ],
    )
    def test_multiplier(self, chart, attacking, defending, expected):
        result = effectiveness(chart, PokemonType(attacking), (PokemonType(defending),))
        assert result == expected

    def test_steel_fairy_is_directional(self, chart):
        """방향을 헷갈리기 쉬운 쌍. 강철로 페어리를 때리면 2배다."""
        steel_hits_fairy = effectiveness(chart, PokemonType.STEEL, (PokemonType.FAIRY,))
        fairy_hits_steel = effectiveness(chart, PokemonType.FAIRY, (PokemonType.STEEL,))
        assert steel_hits_fairy == 2.0
        assert fairy_hits_steel == 0.5


class TestDualTypeDefender:
    """2타입 방어가 전체 폼의 68%다. 4배·0.25배·0배가 여기서 나온다."""

    @pytest.mark.parametrize(
        ("attacking", "defending", "expected"),
        [
            # 4배 — 두 타입 모두 약점
            ("rock", ("fire", "flying"), 4.0),
            ("ice", ("dragon", "flying"), 4.0),
            # 0.25배 — 두 타입 모두 저항
            ("grass", ("fire", "flying"), 0.25),
            ("fighting", ("psychic", "flying"), 0.25),
            # 상쇄 — 약점 × 저항
            ("water", ("fire", "grass"), 1.0),
            # 0배가 다른 배율을 덮는다
            ("ground", ("steel", "flying"), 0.0),
            ("normal", ("ghost", "dark"), 0.0),
            # 한쪽만 걸리는 경우
            ("fire", ("grass", "water"), 1.0),
            ("electric", ("water", "flying"), 4.0),
        ],
    )
    def test_multiplier(self, chart, attacking, defending, expected):
        defenders = tuple(PokemonType(t) for t in defending)
        assert effectiveness(chart, PokemonType(attacking), defenders) == expected

    def test_order_does_not_matter(self, chart):
        a = effectiveness(chart, PokemonType.ROCK, (PokemonType.FIRE, PokemonType.FLYING))
        b = effectiveness(chart, PokemonType.ROCK, (PokemonType.FLYING, PokemonType.FIRE))
        assert a == b == 4.0


class TestResultDomain:
    """반환 가능한 배율은 0 / 0.25 / 0.5 / 1 / 2 / 4 뿐이다 (CLAUDE.md 4장)."""

    def test_all_combinations_produce_valid_multipliers(self, chart):
        allowed = {0.0, 0.25, 0.5, 1.0, 2.0, 4.0}
        types = list(PokemonType)
        seen = set()
        for attacking in types:
            for first in types:
                seen.add(effectiveness(chart, attacking, (first,)))
                for second in types:
                    if first is second:
                        continue
                    seen.add(effectiveness(chart, attacking, (first, second)))
        assert seen <= allowed, f"허용되지 않는 배율: {seen - allowed}"

    def test_every_multiplier_value_is_reachable(self, chart):
        """6개 배율이 모두 실제로 나온다 — 표가 비어 있지 않다는 확인."""
        types = list(PokemonType)
        seen = set()
        for attacking in types:
            for first in types:
                for second in types:
                    defenders = (first,) if first is second else (first, second)
                    seen.add(effectiveness(chart, attacking, defenders))
        assert seen == {0.0, 0.25, 0.5, 1.0, 2.0, 4.0}


class TestInputValidation:
    def test_empty_defender_is_rejected(self, chart):
        with pytest.raises(ValueError):
            effectiveness(chart, PokemonType.FIRE, ())

    def test_too_many_defending_types_is_rejected(self, chart):
        with pytest.raises(ValueError):
            effectiveness(
                chart,
                PokemonType.FIRE,
                (PokemonType.GRASS, PokemonType.FLYING, PokemonType.BUG),
            )

    def test_unknown_type_string_is_rejected(self):
        with pytest.raises(ValueError):
            PokemonType("mystery")

    def test_chart_rejects_unknown_type_in_data(self):
        with pytest.raises(UnknownPokemonType):
            TypeChart.from_mapping({"normal": {"mystery": 2.0}})


class TestChartIsImmutable:
    def test_mutating_source_does_not_affect_chart(self):
        source = {"fire": {"grass": 2.0}}
        chart = TypeChart.from_mapping(source)
        source["fire"]["grass"] = 0.5
        assert effectiveness(chart, PokemonType.FIRE, (PokemonType.GRASS,)) == 2.0
