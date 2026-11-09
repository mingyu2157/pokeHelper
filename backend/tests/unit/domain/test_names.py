"""한영 명칭 매핑 테스트.

★ 이 테스트가 이 프로젝트에서 가장 시끄러워야 한다 ★

ADR-005 가 정한 커버리지 게이트다. 매핑이 하나라도 비어 있으면 빌드를 깬다.
"조용히 영문명이 새어 나가는 것"보다 빌드가 깨지는 게 낫다는 판단이었고,
그 판단을 지키는 장치가 `TestCoverage` 다.
"""

import json
from pathlib import Path

import pytest

from app.domain.exceptions import DuplicateNameMapping, NameNotMapped
from app.domain.names import NameMap

BACKEND_DIR = Path(__file__).resolve().parents[3]
NAME_MAP_PATH = BACKEND_DIR / "data" / "name_map.json"
PENDING_PATH = BACKEND_DIR / "data" / "name_map_pending.json"
POKEMON_PATH = BACKEND_DIR / "data" / "pokemon.json"


@pytest.fixture(scope="module")
def raw_data():
    with NAME_MAP_PATH.open(encoding="utf-8") as file:
        return json.load(file)


@pytest.fixture(scope="module")
def pokemon_ids():
    with POKEMON_PATH.open(encoding="utf-8") as file:
        return set(json.load(file)["pokemon"])


@pytest.fixture(scope="module")
def name_map(raw_data):
    return NameMap.from_mapping(raw_data["pokemon"])


class TestCoverage:
    """★ 미매핑이 하나라도 있으면 빌드를 깬다 (ADR-005 방어선 4)."""

    def test_every_pokemon_is_mapped(self, name_map, pokemon_ids):
        unmapped = sorted(i for i in pokemon_ids if not name_map.has(i))
        assert not unmapped, (
            f"한글명이 없는 포켓몬 {len(unmapped)}개: {unmapped[:20]}. "
            "name_map_pending.json 을 검수해 확정 문자열을 채운다. "
            "영문명으로 때우지 않는다."
        )

    def test_nothing_is_left_pending(self):
        """보류가 남아 있으면 미완성이다. 검수 전에 머지되는 것을 막는다."""
        if not PENDING_PATH.exists():
            return
        with PENDING_PATH.open(encoding="utf-8") as file:
            pending = json.load(file)["pokemon"]
        assert not pending, (
            f"검수 대기 {len(pending)}건: {sorted(pending)[:20]}. "
            "각 항목의 reason 을 보고 확정 문자열을 넣는다."
        )

    def test_map_has_no_extra_ids(self, raw_data, pokemon_ids):
        """마스터 시드에 없는 id 가 매핑에 있으면 폼 이름이 바뀐 것이다."""
        extra = sorted(set(raw_data["pokemon"]) - pokemon_ids)
        assert not extra, f"마스터 시드에 없는 id: {extra}"


class TestDataFile:
    def test_counts_agree(self, raw_data):
        meta = raw_data["_meta"]
        assert meta["confirmed"] == len(raw_data["pokemon"])
        assert meta["confirmed"] + meta["pending"] == meta["total"]

    def test_every_entry_records_what_was_verified(self, raw_data):
        """근거 없는 매핑은 두지 않는다. 무엇으로 검증했는지 데이터가 말해야 한다."""
        for showdown_id, record in raw_data["pokemon"].items():
            assert record["verified"] == ["base_stats", "types"], showdown_id
            assert record["pokeapi_slug"], showdown_id

    def test_korean_names_are_korean(self, raw_data):
        """영문명이 섞여 들어오면 fail-closed 가 뚫린 것이다."""
        for showdown_id, record in raw_data["pokemon"].items():
            korean = record["ko"]
            assert korean.strip() == korean, showdown_id
            assert any("가" <= ch <= "힣" for ch in korean), f"{showdown_id}: {korean!r}"

    def test_no_english_letters_in_names(self, raw_data):
        """'Garchomp' 같은 값이 남아 있는지 본다. 알파벳이 섞인 표기는 검수 대상이다."""
        suspicious = {
            showdown_id: record["ko"]
            for showdown_id, record in raw_data["pokemon"].items()
            if any(ch.isascii() and ch.isalpha() for ch in record["ko"])
        }
        assert not suspicious, f"알파벳이 섞인 한글명: {suspicious}"


class TestLookup:
    def test_id_to_korean(self, name_map):
        assert name_map.ko("garchomp") == "한카리아스"

    def test_korean_to_id(self, name_map):
        assert name_map.id_of("한카리아스") == "garchomp"

    def test_round_trip(self, name_map):
        for showdown_id in name_map.ids():
            assert name_map.id_of(name_map.ko(showdown_id)) == showdown_id

    def test_unknown_id_fails_closed(self, name_map):
        """영문명을 돌려주지 않는다. 비슷한 것도 찾아주지 않는다 (ADR-005)."""
        with pytest.raises(NameNotMapped):
            name_map.ko("mystery")

    def test_unknown_korean_fails_closed(self, name_map):
        with pytest.raises(NameNotMapped):
            name_map.id_of("없는포켓몬")

    def test_case_is_not_normalised(self, name_map):
        with pytest.raises(NameNotMapped):
            name_map.ko("Garchomp")

    def test_korean_lookup_is_exact(self, name_map):
        """공백 하나 차이도 통과시키지 않는다. 퍼지 매칭 금지의 구체적 의미다."""
        with pytest.raises(NameNotMapped):
            name_map.id_of("한카리아스 ")

    def test_has_does_not_raise(self, name_map):
        assert name_map.has("garchomp") is True
        assert name_map.has("mystery") is False

    def test_ids_are_sorted(self, name_map):
        assert list(name_map.ids()) == sorted(name_map.ids())


class TestConstruction:
    def test_rejects_duplicate_korean_name(self):
        """한 한글명이 두 id 에 걸리면 조회가 어느 쪽인지 알 수 없다."""
        with pytest.raises(DuplicateNameMapping):
            NameMap.from_mapping({"a": {"ko": "같은이름"}, "b": {"ko": "같은이름"}})

    def test_rejects_empty_korean_name(self):
        with pytest.raises(DuplicateNameMapping):
            NameMap.from_mapping({"a": {"ko": "   "}})

    def test_rejects_missing_key(self):
        with pytest.raises(KeyError):
            NameMap.from_mapping({"a": {"pokeapi_slug": "x"}})

    def test_strips_surrounding_whitespace_once(self):
        """출처가 공백을 붙여 보내는 경우를 흡수한다. 조회는 여전히 정확 일치다."""
        mapping = NameMap.from_mapping({"a": {"ko": " 한카리아스 "}})
        assert mapping.ko("a") == "한카리아스"
        with pytest.raises(NameNotMapped):
            mapping.id_of(" 한카리아스 ")
