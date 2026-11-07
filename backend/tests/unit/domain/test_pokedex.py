"""포켓몬 마스터 시드 조회 테스트.

데이터는 `backend/data/pokemon.json` (championsbattledata 인덱스에서 생성).
domain 은 파일을 읽지 않으므로 테스트가 읽어 주입한다 (ADR-007).
"""

import json
from pathlib import Path

import pytest

from app.domain.exceptions import UnknownPokemon, UnknownPokemonType
from app.domain.pokedex import Pokedex, PokemonSpec
from app.domain.typechart import PokemonType

POKEMON_PATH = Path(__file__).resolve().parents[3] / "data" / "pokemon.json"


@pytest.fixture(scope="module")
def raw_data():
    with POKEMON_PATH.open(encoding="utf-8") as file:
        return json.load(file)


@pytest.fixture(scope="module")
def pokedex(raw_data):
    return Pokedex.from_mapping(raw_data["pokemon"])


class TestDataFile:
    """생성 데이터의 불변식. 깨지면 출처가 변한 것이다."""

    def test_has_entries(self, raw_data):
        assert raw_data["_meta"]["count"] == len(raw_data["pokemon"])
        assert len(raw_data["pokemon"]) > 200

    def test_base_stats_are_in_range(self, raw_data):
        for showdown_id, record in raw_data["pokemon"].items():
            for stat, value in record["base_stats"].items():
                assert 1 <= value <= 255, f"{showdown_id}.{stat} = {value}"

    def test_types_are_valid(self, raw_data):
        valid = {t.value for t in PokemonType}
        for showdown_id, record in raw_data["pokemon"].items():
            types = record["types"]
            assert 1 <= len(types) <= 2, showdown_id
            assert len(set(types)) == len(types), showdown_id
            for name in types:
                assert name in valid, f"{showdown_id}: {name}"

    def test_every_pokemon_has_an_ability(self, raw_data):
        for showdown_id, record in raw_data["pokemon"].items():
            assert record["abilities"], showdown_id

    def test_no_korean_names(self, raw_data):
        """한영 매핑은 name_map.json 소관이다 (ADR-005). 여기 두면 출처가 갈린다."""
        text = json.dumps(raw_data, ensure_ascii=False)
        assert not any(
            "가" <= ch <= "힣"
            for ch in text.replace(raw_data["_meta"]["note"], "").replace(
                raw_data["_meta"]["key"], ""
            )
        )


class TestOffsetWasStripped:
    """★ API 의 hp/attack 은 종족값이 아니라 노력치 0·무보정 실수치다.

    오프셋을 벗기지 않으면 calc_hp/calc_stat 이 한 번 더 더해서 전부 틀어진다.
    본가 종족값과 대조해 제대로 벗겼는지 고정한다 (2026-11-08 PokéAPI 20종 검증).
    """

    @pytest.mark.parametrize(
        ("showdown_id", "expected"),
        [
            ("garchomp", (108, 130, 95, 80, 85, 102)),
            ("snorlax", (160, 110, 65, 65, 110, 30)),
            ("pikachu", (35, 55, 40, 50, 50, 90)),
            ("dragonite", (91, 134, 95, 100, 100, 80)),
        ],
    )
    def test_matches_known_base_stats(self, pokedex, showdown_id, expected):
        spec = pokedex.get(showdown_id)
        stats = spec.base_stats
        assert (
            stats.hp,
            stats.attack,
            stats.defense,
            stats.sp_attack,
            stats.sp_defense,
            stats.speed,
        ) == expected

    def test_hp_is_not_inflated_by_the_offset(self, pokedex):
        """한카리아스 HP 가 183 이면 오프셋을 안 벗긴 것이다."""
        assert pokedex.get("garchomp").base_stats.hp != 183

    def test_stats_feed_the_engine_directly(self, pokedex):
        """종족값을 그대로 calc 에 넣으면 알려진 실수치가 나와야 한다."""
        from app.domain.nature import NatureModifier
        from app.domain.stats import calc_stat

        speed = pokedex.get("garchomp").base_stats.speed
        assert calc_stat(speed, 32, NatureModifier.UP) == 169  # 최속 한카리아스


class TestLookup:
    def test_returns_a_spec(self, pokedex):
        spec = pokedex.get("garchomp")
        assert isinstance(spec, PokemonSpec)
        assert spec.showdown_id == "garchomp"
        assert spec.types == (PokemonType.DRAGON, PokemonType.GROUND)

    def test_unknown_id_fails_closed(self, pokedex):
        """추측하지 않는다 (ADR-005)."""
        with pytest.raises(UnknownPokemon):
            pokedex.get("mystery")

    def test_case_is_not_normalised(self, pokedex):
        with pytest.raises(UnknownPokemon):
            pokedex.get("Garchomp")

    def test_lists_every_id(self, pokedex, raw_data):
        ids = pokedex.ids()
        assert set(ids) == set(raw_data["pokemon"])
        assert list(ids) == sorted(ids)

    def test_forms_are_separate_entries(self, pokedex):
        """폼은 showdownId 로 구분된다 (CLAUDE.md 8-1)."""
        base = pokedex.get("raichu")
        alola = pokedex.get("raichualola")
        assert base.types != alola.types


class TestConstruction:
    def test_rejects_unknown_type(self):
        with pytest.raises(UnknownPokemonType):
            Pokedex.from_mapping(
                {
                    "x": {
                        "name": "X",
                        "display_name": "X",
                        "form_kind": "Base",
                        "types": ["mystery"],
                        "base_stats": {
                            "hp": 1,
                            "attack": 1,
                            "defense": 1,
                            "sp_attack": 1,
                            "sp_defense": 1,
                            "speed": 1,
                        },
                        "abilities": ["A"],
                    }
                }
            )

    def test_rejects_missing_stats(self):
        with pytest.raises(TypeError):
            Pokedex.from_mapping(
                {
                    "x": {
                        "name": "X",
                        "display_name": "X",
                        "form_kind": "Base",
                        "types": ["normal"],
                        "base_stats": {"hp": 1},
                        "abilities": ["A"],
                    }
                }
            )
