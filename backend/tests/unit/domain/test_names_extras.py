"""성격·특성 한영 매핑 테스트 (Phase 2-3b).

포켓몬 매핑과 같은 커버리지 게이트다 — 미매핑이 하나라도 있으면 빌드를 깬다.
다만 **검증 재료가 다르다.** 종족값이 없는 대상이라 각자가 가진 다른 사실을 썼다:
성격은 상승/하락 스탯 쌍, 특성은 보유 포켓몬 목록.
"""

import json
from pathlib import Path

import pytest

from app.domain.exceptions import NameNotMapped
from app.domain.names import NameMap

BACKEND_DIR = Path(__file__).resolve().parents[3]
NATURES_MAP_PATH = BACKEND_DIR / "data" / "name_map_natures.json"
ABILITIES_MAP_PATH = BACKEND_DIR / "data" / "name_map_abilities.json"
NATURES_PATH = BACKEND_DIR / "data" / "natures.json"
POKEMON_PATH = BACKEND_DIR / "data" / "pokemon.json"


def _read(path: Path):
    with path.open(encoding="utf-8") as file:
        return json.load(file)


@pytest.fixture(scope="module")
def nature_raw():
    return _read(NATURES_MAP_PATH)


@pytest.fixture(scope="module")
def ability_raw():
    return _read(ABILITIES_MAP_PATH)


@pytest.fixture(scope="module")
def nature_names(nature_raw):
    return NameMap.from_mapping(nature_raw["natures"])


@pytest.fixture(scope="module")
def ability_names(ability_raw):
    return NameMap.from_mapping(ability_raw["abilities"])


class TestNatureCoverage:
    """★ 성격 25개 전부 매핑돼야 한다. 하나라도 비면 빌드를 깬다."""

    def test_covers_every_nature(self, nature_names):
        seeded = set(_read(NATURES_PATH)["natures"])
        unmapped = sorted(n for n in seeded if not nature_names.has(n))
        assert not unmapped, f"한글명이 없는 성격: {unmapped}"

    def test_has_exactly_the_seeded_natures(self, nature_raw):
        assert set(nature_raw["natures"]) == set(_read(NATURES_PATH)["natures"])

    def test_every_entry_was_verified_by_the_stat_pair(self, nature_raw):
        """근거를 데이터가 말해야 한다. 성격의 방어선 2는 보정 쌍이다."""
        for key, record in nature_raw["natures"].items():
            assert record["verified"] == ["nature_stat_pair"], key


class TestAbilityCoverage:
    """★ `pokemon.json` 이 참조하는 특성 전부가 매핑돼야 한다."""

    def test_covers_every_ability_in_use(self, ability_names):
        seeded = {
            ability
            for record in _read(POKEMON_PATH)["pokemon"].values()
            for ability in record["abilities"]
        }
        import re

        unmapped = sorted(
            ability
            for ability in seeded
            if not ability_names.has(re.sub(r"[^a-z0-9]", "", ability.lower()))
        )
        assert not unmapped, (
            f"한글명이 없는 특성 {len(unmapped)}개: {unmapped[:20]}. "
            "챔피언스/ZA 전용이면 사람이 확정 문자열을 넣어야 한다."
        )

    def test_every_entry_was_verified_by_owners(self, ability_raw):
        """'이 특성을 가진 포켓몬이 겹친다'가 특성의 방어선 2다."""
        for key, record in ability_raw["abilities"].items():
            assert record["verified"] == ["ability_owners"], key
            assert record["owner_overlap"] >= 1, key

    def test_keeps_the_english_name(self, ability_raw):
        """사용률 API 가 영문 표시명을 주므로 역추적 가능해야 한다."""
        import re

        for key, record in ability_raw["abilities"].items():
            assert record["en"], key
            assert re.sub(r"[^a-z0-9]", "", record["en"].lower()) == key


class TestNamesAreKorean:
    @pytest.mark.parametrize("fixture_name", ["nature_raw", "ability_raw"])
    def test_no_english_letters(self, fixture_name, request):
        raw = request.getfixturevalue(fixture_name)
        key = "natures" if fixture_name == "nature_raw" else "abilities"
        suspicious = {
            entry_key: record["ko"]
            for entry_key, record in raw[key].items()
            if any(ch.isascii() and ch.isalpha() for ch in record["ko"])
        }
        assert not suspicious, f"알파벳이 섞인 한글명: {suspicious}"


class TestLookup:
    def test_nature_round_trip(self, nature_names):
        assert nature_names.ko("jolly") == "명랑"
        assert nature_names.id_of("명랑") == "jolly"
        # 천진난만은 Naive 다 — 스피드↑ 특방↓. 보정 쌍이 다르다.
        assert nature_names.ko("naive") == "천진난만"

    def test_ability_round_trip(self, ability_names):
        assert ability_names.ko("roughskin") == "까칠한피부"
        assert ability_names.id_of("까칠한피부") == "roughskin"

    def test_every_entry_round_trips(self, nature_names, ability_names):
        for mapping in (nature_names, ability_names):
            for key in mapping.ids():
                assert mapping.id_of(mapping.ko(key)) == key

    def test_unknown_fails_closed(self, nature_names, ability_names):
        with pytest.raises(NameNotMapped):
            nature_names.ko("supercalm")
        with pytest.raises(NameNotMapped):
            ability_names.ko("megapower")

    def test_hyphenated_ability_id_is_not_used(self, ability_names):
        """id 는 구분자가 없는 형태다. PokéAPI 슬러그로는 조회되지 않는다."""
        with pytest.raises(NameNotMapped):
            ability_names.ko("rough-skin")


class TestNameMapIsReusable:
    """★ 포켓몬 전용이 아니어야 한다 — 같은 클래스를 세 종류에 쓴다.

    이 테스트가 깨지면 `NameMap` 에 포켓몬 전용 가정이 들어간 것이다.
    """

    def test_same_class_serves_all_three(self, nature_names, ability_names):
        from app.core.seed import pokemon_name_map

        assert isinstance(nature_names, NameMap)
        assert isinstance(ability_names, NameMap)
        assert isinstance(pokemon_name_map(), NameMap)
