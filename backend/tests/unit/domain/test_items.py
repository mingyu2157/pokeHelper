"""도구 효과 해석 테스트.

도메인은 도구를 "이미 분류된 효과"로만 다룬다. JSON 로딩은 바깥 계층 몫이다 (ADR-007).
"""

import json
from pathlib import Path

import pytest

from app.domain.exceptions import UnknownItemEffect, UnknownPokemonType
from app.domain.items import (
    ItemCatalog,
    ItemEffectKind,
    defensive_multiplier,
    grants_immunity,
    offensive_multiplier,
)
from app.domain.typechart import PokemonType

ITEMS_PATH = Path(__file__).resolve().parents[3] / "data" / "items.json"


@pytest.fixture(scope="module")
def raw_data():
    with ITEMS_PATH.open(encoding="utf-8") as file:
        return json.load(file)


@pytest.fixture(scope="module")
def catalog(raw_data):
    return ItemCatalog.from_mapping(raw_data["items"])


class TestDataFile:
    def test_every_item_has_a_known_kind(self, raw_data):
        known = {kind.name for kind in ItemEffectKind}
        for item_id, entry in raw_data["items"].items():
            assert entry["kind"] in known, f"{item_id}: {entry['kind']}"

    def test_every_item_records_a_source(self, raw_data):
        allowed = {"champions-helper", "mainline-assumed", "verified"}
        for item_id, entry in raw_data["items"].items():
            assert entry.get("source") in allowed, f"{item_id}"

    def test_damage_items_have_multipliers(self, raw_data):
        needs_multiplier = {
            "DAMAGE_FLAT",
            "DAMAGE_TYPE",
            "DAMAGE_SUPER_EFFECTIVE",
            "DEFENSIVE_TYPE_RESIST",
            "SPEED",
        }
        for item_id, entry in raw_data["items"].items():
            if entry["kind"] in needs_multiplier:
                assert isinstance(entry.get("multiplier"), (int, float)), item_id

    def test_type_scoped_items_name_a_type(self, raw_data):
        needs_type = {"DAMAGE_TYPE", "DEFENSIVE_TYPE_RESIST", "TYPE_IMMUNITY"}
        for item_id, entry in raw_data["items"].items():
            if entry["kind"] in needs_type:
                assert "move_type" in entry, item_id
                PokemonType(entry["move_type"])  # 알 수 없는 타입이면 예외

    def test_life_orb_value_comes_from_champions_source(self, raw_data):
        """유일하게 챔피언스 자료로 확인된 수치다."""
        assert raw_data["items"]["lifeorb"]["source"] == "champions-helper"
        assert raw_data["items"]["lifeorb"]["multiplier"] == 1.3


class TestOffensiveMultiplier:
    def test_unknown_item_is_neutral(self, catalog):
        assert offensive_multiplier(None, PokemonType.FIRE, 1.0) == 1.0

    def test_flat_item_applies_to_everything(self, catalog):
        orb = catalog.get("lifeorb")
        assert offensive_multiplier(orb, PokemonType.FIRE, 1.0) == 1.3
        assert offensive_multiplier(orb, PokemonType.WATER, 0.5) == 1.3

    def test_type_item_applies_only_to_matching_type(self, catalog):
        magnet = catalog.get("magnet")
        assert offensive_multiplier(magnet, PokemonType.ELECTRIC, 1.0) == 1.2
        assert offensive_multiplier(magnet, PokemonType.FIRE, 1.0) == 1.0

    @pytest.mark.parametrize(
        ("effectiveness", "expected"),
        [(4.0, 1.2), (2.0, 1.2), (1.0, 1.0), (0.5, 1.0), (0.0, 1.0)],
    )
    def test_expert_belt_needs_super_effective(self, catalog, effectiveness, expected):
        belt = catalog.get("expertbelt")
        assert offensive_multiplier(belt, PokemonType.FIRE, effectiveness) == expected

    def test_defensive_item_does_not_boost_offense(self, catalog):
        shuca = catalog.get("shucaberry")
        assert offensive_multiplier(shuca, PokemonType.GROUND, 2.0) == 1.0

    def test_non_damage_item_is_neutral(self, catalog):
        assert offensive_multiplier(catalog.get("leftovers"), PokemonType.FIRE, 2.0) == 1.0
        assert offensive_multiplier(catalog.get("choicescarf"), PokemonType.FIRE, 2.0) == 1.0


class TestDefensiveMultiplier:
    def test_unknown_item_is_neutral(self, catalog):
        assert defensive_multiplier(None, PokemonType.GROUND, 2.0) == 1.0

    def test_shuca_halves_super_effective_ground(self, catalog):
        shuca = catalog.get("shucaberry")
        assert defensive_multiplier(shuca, PokemonType.GROUND, 2.0) == 0.5
        assert defensive_multiplier(shuca, PokemonType.GROUND, 4.0) == 0.5

    def test_shuca_does_nothing_when_not_super_effective(self, catalog):
        shuca = catalog.get("shucaberry")
        assert defensive_multiplier(shuca, PokemonType.GROUND, 1.0) == 1.0

    def test_shuca_does_nothing_against_other_types(self, catalog):
        shuca = catalog.get("shucaberry")
        assert defensive_multiplier(shuca, PokemonType.FIRE, 2.0) == 1.0

    def test_offensive_item_does_not_reduce_damage(self, catalog):
        assert defensive_multiplier(catalog.get("lifeorb"), PokemonType.GROUND, 2.0) == 1.0


class TestImmunity:
    def test_air_balloon_blocks_ground(self, catalog):
        assert grants_immunity(catalog.get("airballoon"), PokemonType.GROUND) is True

    def test_air_balloon_does_not_block_other_types(self, catalog):
        assert grants_immunity(catalog.get("airballoon"), PokemonType.FIRE) is False

    def test_other_items_grant_no_immunity(self, catalog):
        assert grants_immunity(catalog.get("lifeorb"), PokemonType.GROUND) is False
        assert grants_immunity(None, PokemonType.GROUND) is False


class TestCatalog:
    def test_missing_item_returns_none(self, catalog):
        assert catalog.get("nonexistent") is None

    def test_unknown_kind_is_rejected(self):
        with pytest.raises(UnknownItemEffect):
            ItemCatalog.from_mapping({"x": {"name_en": "X", "kind": "MYSTERY"}})

    def test_unknown_type_is_rejected(self):
        with pytest.raises(UnknownPokemonType):
            ItemCatalog.from_mapping(
                {"x": {"name_en": "X", "kind": "DAMAGE_TYPE", "move_type": "mystery"}}
            )

    def test_catalog_covers_the_seeded_items(self, catalog, raw_data):
        for item_id in raw_data["items"]:
            assert catalog.get(item_id) is not None
