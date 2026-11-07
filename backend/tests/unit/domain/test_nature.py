"""성격 카탈로그 테스트.

데이터는 `backend/data/natures.json` (PokéAPI 생성).
domain 은 파일을 읽지 않으므로 테스트가 읽어 주입한다.
"""

import json
from pathlib import Path

import pytest

from app.domain.exceptions import InvalidNatureSpread, UnknownNature
from app.domain.nature import NatureCatalog, NatureModifier, StatKey

NATURES_PATH = Path(__file__).resolve().parents[3] / "data" / "natures.json"


@pytest.fixture(scope="module")
def raw_data():
    with NATURES_PATH.open(encoding="utf-8") as file:
        return json.load(file)


@pytest.fixture(scope="module")
def catalog(raw_data):
    return NatureCatalog.from_mapping(raw_data["natures"])


class TestDataFile:
    def test_twenty_five_natures(self, raw_data):
        assert len(raw_data["natures"]) == 25

    def test_five_are_neutral(self, raw_data):
        neutral = [n for n, s in raw_data["natures"].items() if s["raised"] is None]
        assert len(neutral) == 5

    def test_raised_and_lowered_are_always_paired(self, raw_data):
        for name, spec in raw_data["natures"].items():
            assert (spec["raised"] is None) == (spec["lowered"] is None), name

    def test_never_raises_and_lowers_the_same_stat(self, raw_data):
        for name, spec in raw_data["natures"].items():
            if spec["raised"] is not None:
                assert spec["raised"] != spec["lowered"], name

    def test_hp_is_never_affected(self, raw_data):
        for name, spec in raw_data["natures"].items():
            assert "HP" not in (spec["raised"], spec["lowered"]), name

    def test_non_neutral_combinations_are_unique(self, raw_data):
        """상승 5종 × 하락 4종 = 20. 중복이 있으면 수집이 깨진 것이다."""
        combos = [
            (s["raised"], s["lowered"])
            for s in raw_data["natures"].values()
            if s["raised"] is not None
        ]
        assert len(combos) == 20
        assert len(set(combos)) == 20


class TestCatalogLookup:
    @pytest.mark.parametrize(
        ("nature", "raised", "lowered"),
        [
            ("adamant", StatKey.ATTACK, StatKey.SP_ATTACK),
            ("jolly", StatKey.SPEED, StatKey.SP_ATTACK),
            ("timid", StatKey.SPEED, StatKey.ATTACK),
            ("modest", StatKey.SP_ATTACK, StatKey.ATTACK),
            ("bold", StatKey.DEFENSE, StatKey.ATTACK),
            ("careful", StatKey.SP_DEFENSE, StatKey.SP_ATTACK),
        ],
    )
    def test_known_natures(self, catalog, nature, raised, lowered):
        spec = catalog.get(nature)
        assert spec.raised is raised
        assert spec.lowered is lowered

    @pytest.mark.parametrize("nature", ["hardy", "docile", "bashful", "quirky", "serious"])
    def test_neutral_natures(self, catalog, nature):
        spec = catalog.get(nature)
        assert spec.raised is None
        assert spec.lowered is None
        assert spec.is_neutral is True

    def test_non_neutral_is_not_marked_neutral(self, catalog):
        assert catalog.get("jolly").is_neutral is False

    def test_lists_every_name(self, catalog, raw_data):
        """API 가 유효한 성격 목록을 노출할 때 쓴다."""
        names = catalog.names()
        assert len(names) == 25
        assert set(names) == set(raw_data["natures"])
        assert list(names) == sorted(names)

    def test_unknown_nature_fails_closed(self, catalog):
        """추측하지 않는다. 모르는 성격은 실패시킨다 (ADR-005 의 원칙)."""
        with pytest.raises(UnknownNature):
            catalog.get("mystery")

    def test_case_is_not_normalised(self, catalog):
        """정확 일치만 허용한다. 대소문자 보정도 하지 않는다."""
        with pytest.raises(UnknownNature):
            catalog.get("Adamant")


class TestModifiers:
    def test_produces_modifier_map(self, catalog):
        modifiers = catalog.get("jolly").modifiers()
        assert modifiers == {
            StatKey.SPEED: NatureModifier.UP,
            StatKey.SP_ATTACK: NatureModifier.DOWN,
        }

    def test_neutral_produces_empty_map(self, catalog):
        assert catalog.get("hardy").modifiers() == {}

    def test_modifiers_feed_the_solver(self, catalog):
        """BuildTarget 의 성격 쌍 검증을 그대로 통과해야 한다."""
        from app.domain.ev_solver import BuildTarget
        from app.domain.models import BaseStats

        base = BaseStats(hp=108, attack=130, defense=95, sp_attack=80, sp_defense=85, speed=102)
        for name in ("jolly", "adamant", "hardy"):
            BuildTarget(base_stats=base, natures=catalog.get(name).modifiers())


class TestCatalogConstruction:
    def test_rejects_unknown_stat_id(self):
        with pytest.raises(UnknownNature):
            NatureCatalog.from_mapping({"x": {"raised": "MYSTERY", "lowered": "SPEED"}})

    def test_rejects_unpaired_entry(self):
        with pytest.raises(InvalidNatureSpread):
            NatureCatalog.from_mapping({"x": {"raised": "ATTACK", "lowered": None}})

    def test_rejects_same_stat_raised_and_lowered(self):
        with pytest.raises(InvalidNatureSpread):
            NatureCatalog.from_mapping({"x": {"raised": "ATTACK", "lowered": "ATTACK"}})
