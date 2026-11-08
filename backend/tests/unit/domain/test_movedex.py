"""기술 마스터 시드 조회 테스트.

데이터는 `backend/data/moves.json` (op.gg 에서 생성, champions-helper·smartnuo 교차검증).
domain 은 파일을 읽지 않으므로 테스트가 읽어 주입한다 (ADR-007).
"""

import json
from pathlib import Path

import pytest

from app.domain.exceptions import (
    MoveHasNoFixedPower,
    MoveIsNotDamaging,
    UnknownMove,
    UnknownMoveCategory,
    UnknownPokemonType,
)
from app.domain.models import MoveCategory, MoveSpec
from app.domain.movedex import MoveDex, MoveEntry
from app.domain.typechart import PokemonType

BACKEND_DIR = Path(__file__).resolve().parents[3]
MOVES_PATH = BACKEND_DIR / "data" / "moves.json"
NAME_MAP_PATH = BACKEND_DIR / "data" / "name_map_moves.json"

# 챔피언스가 PP 를 이 다섯 값으로 정규화했다 (본가는 5~40 제각각).
VALID_PP = {1, 8, 12, 16, 20}
MAX_POWER = 300


def _entry(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "slug": "test-move",
        "type": "normal",
        "category": "physical",
        "power": 50,
        "accuracy": 100,
        "pp": 20,
        "priority": 0,
        "contact": True,
        "flags": [],
        "target": "selected",
    }
    base.update(overrides)
    return base


@pytest.fixture(scope="module")
def raw_data():
    with MOVES_PATH.open(encoding="utf-8") as file:
        return json.load(file)


@pytest.fixture(scope="module")
def name_map():
    with NAME_MAP_PATH.open(encoding="utf-8") as file:
        return json.load(file)


@pytest.fixture(scope="module")
def movedex(raw_data):
    return MoveDex.from_mapping(raw_data["moves"])


class TestDataFile:
    """생성 데이터의 불변식. 깨지면 출처가 변한 것이다."""

    def test_has_entries(self, raw_data):
        assert raw_data["_meta"]["count"] == len(raw_data["moves"])
        assert len(raw_data["moves"]) > 500

    def test_id_is_the_slug_without_separators(self, raw_data):
        """사용률 API 의 영문 표시명과 조인하려면 이 환원이 성립해야 한다."""
        for move_id, record in raw_data["moves"].items():
            assert move_id == record["slug"].replace("-", ""), move_id

    def test_power_is_a_multiple_of_five_or_absent(self, raw_data):
        for move_id, record in raw_data["moves"].items():
            power = record["power"]
            if power is None:
                continue
            assert 0 < power <= MAX_POWER, f"{move_id}: {power}"
            assert power % 5 == 0, f"{move_id}: {power}"

    def test_status_moves_have_no_power(self, raw_data):
        for move_id, record in raw_data["moves"].items():
            if record["category"] == "status":
                assert record["power"] is None, move_id

    def test_pp_is_one_of_the_champions_values(self, raw_data):
        for move_id, record in raw_data["moves"].items():
            assert record["pp"] in VALID_PP, f"{move_id}: {record['pp']}"

    def test_accuracy_is_a_percentage_or_absent(self, raw_data):
        for move_id, record in raw_data["moves"].items():
            accuracy = record["accuracy"]
            assert accuracy is None or 1 <= accuracy <= 100, move_id

    def test_priority_is_within_the_known_range(self, raw_data):
        for move_id, record in raw_data["moves"].items():
            assert -7 <= record["priority"] <= 5, f"{move_id}: {record['priority']}"

    def test_every_move_has_a_known_type_and_category(self, raw_data):
        types = {t.value for t in PokemonType}
        for move_id, record in raw_data["moves"].items():
            assert record["type"] in types, move_id
            assert record["category"] in {"physical", "special", "status"}, move_id

    def test_no_korean_names(self, raw_data):
        """한영 매핑은 name_map_moves.json 소관이다 (ADR-005)."""
        text = json.dumps(raw_data["moves"], ensure_ascii=False)
        assert not any("가" <= ch <= "힣" for ch in text)


class TestNameMap:
    """한영 매핑은 오류 허용치가 0인 구간이다 (§8-4)."""

    def test_covers_every_move(self, raw_data, name_map):
        assert set(name_map["moves"]) == set(raw_data["moves"])

    def test_mapping_is_unique_in_both_directions(self, name_map):
        korean = [record["ko"] for record in name_map["moves"].values()]
        assert len(set(korean)) == len(korean)
        assert len(set(name_map["moves"])) == len(name_map["moves"])

    def test_every_korean_name_is_present(self, name_map):
        for move_id, record in name_map["moves"].items():
            assert record["ko"].strip(), move_id


class TestChampionsValuesAreNotMainline:
    """★ 본가 데이터로 바꿔치기하면 깨지는 골든 케이스.

    챔피언스 기술 수치는 본가와 다르다. 누군가 PokéAPI 나 Showdown 파생 데이터로
    이 파일을 재생성하면 아래가 전부 틀어진다 (ADR-009).
    """

    @pytest.mark.parametrize(
        ("move_id", "field", "champions", "mainline"),
        [
            ("beakblast", "power", 120, 100),
            ("meteorassault", "power", 170, 150),
            ("mountaingale", "power", 120, 100),
            ("psyshieldbash", "power", 90, 70),
            ("slash", "power", 80, 70),
            ("spiritshackle", "power", 90, 80),
            ("firstimpression", "power", 100, 90),
            ("snipeshot", "power", 85, 80),
            ("thunderbolt", "pp", 16, 15),
            ("steelwing", "pp", 20, 25),
            ("highhorsepower", "pp", 12, 10),
            ("makeitrain", "accuracy", 95, 100),
        ],
    )
    def test_patched_values(self, movedex, move_id, field, champions, mainline):
        assert champions != mainline, "케이스가 본가와 같으면 검증력이 없다"
        assert getattr(movedex.get(move_id), field) == champions

    def test_dragon_claw_is_a_slicing_move(self, movedex):
        """챔피언스가 베기로 재분류했다. 본가에는 이 플래그가 없다."""
        assert movedex.get("dragonclaw").has_flag("slicing")

    def test_cross_poison_is_a_slicing_move(self, movedex):
        assert movedex.get("crosspoison").has_flag("slicing")


class TestFlagsWeNamedOurselves:
    """★ op.gg 의 '압박'·'상승' 은 본가에 영문 이름이 없어 우리가 키를 정했다.

    데이터에 해석이 끼어든 유일한 지점이라 멤버를 골든으로 고정한다.
    출처가 분류를 바꾸면 조용히 넘어가지 말고 여기서 깨져야 한다.
    """

    def test_minimize_boosted_members(self, movedex):
        """상대의 '작아지기'를 무시하고 명중하며 위력 2배. 그룹으로 쓸 수 있다."""
        assert {entry.move_id for entry in movedex.with_flag("minimize_boosted")} == {
            "bodyslam",
            "dragonrush",
            "supercellslam",
            "flyingpress",
            "heavyslam",
            "heatcrash",
        }

    def test_rises_members(self, movedex):
        """'상승'은 **기계적 공통점이 없다.** 멤버가 세 갈래로 갈린다.

        공중날기·튕겨오르기 = 1턴차 회피 후 2턴차 데미지
        전자부유           = 5턴간 '부유' 특성 (땅 타입 무효)
        무릎차기·플라잉프레스 = 효과 없음 (설명문 표현만)

        그래서 이 플래그로는 아무 판단도 하지 않는다. 이름도 성질을 주장하지 않는
        `rises` 다 — `airborne` 은 없는 공통 성질을 주장해서 바꿨다.
        """
        assert {entry.move_id for entry in movedex.with_flag("rises")} == {
            "fly",
            "bounce",
            "highjumpkick",
            "magnetrise",
            "flyingpress",
        }

    def test_airborne_is_not_used_as_a_flag_name(self, raw_data):
        """없는 공통 성질을 주장하는 이름이 다시 들어오면 막는다."""
        assert "airborne" not in json.dumps(raw_data["moves"])

    def test_metadata_records_which_groups_are_usable(self, raw_data):
        """플래그를 계산에 쓸 수 있는지를 데이터가 직접 말해야 한다."""
        named = raw_data["_meta"]["flags_named_by_us"]
        assert named["minimize_boosted"]["usable_as_a_group"] is True
        assert named["rises"]["usable_as_a_group"] is False


class TestLookup:
    def test_returns_an_entry(self, movedex):
        entry = movedex.get("earthquake")
        assert isinstance(entry, MoveEntry)
        assert entry.move_type is PokemonType.GROUND
        assert entry.category is MoveCategory.PHYSICAL
        assert entry.power == 100

    def test_unknown_id_fails_closed(self, movedex):
        with pytest.raises(UnknownMove):
            movedex.get("hyperbeamofdoom")

    def test_case_is_not_normalised(self, movedex):
        with pytest.raises(UnknownMove):
            movedex.get("Earthquake")

    def test_hyphenated_slug_is_not_an_id(self, movedex):
        """id 는 구분자가 없는 형태다. slug 로는 조회되지 않는다."""
        with pytest.raises(UnknownMove):
            movedex.get("beak-blast")

    def test_lists_every_id_sorted(self, movedex, raw_data):
        ids = movedex.ids()
        assert set(ids) == set(raw_data["moves"])
        assert list(ids) == sorted(ids)

    def test_with_flag_collects_a_group(self, movedex):
        punching = movedex.with_flag("punch")
        slugs = {entry.slug for entry in punching}
        assert "fire-punch" in slugs
        assert all(entry.has_flag("punch") for entry in punching)
        assert "earthquake" not in {entry.move_id for entry in punching}


class TestContactAndFlags:
    def test_contact_is_recorded(self, movedex):
        assert movedex.get("closecombat").contact is True
        assert movedex.get("thunderbolt").contact is False

    def test_every_move_has_a_contact_decision(self, raw_data):
        """op.gg 는 모든 기술에 접촉/비접촉을 하나씩 준다. 빠지면 파싱이 깨진 것이다."""
        for move_id, record in raw_data["moves"].items():
            assert isinstance(record["contact"], bool), move_id

    def test_contact_is_not_in_the_flag_list(self, raw_data):
        """접촉은 `contact` 불리언이 소유한다. 두 군데 두면 어긋난다."""
        for move_id, record in raw_data["moves"].items():
            assert "contact" not in record["flags"], move_id
            assert "noncontact" not in record["flags"], move_id


class TestDerivedProperties:
    def test_status_move_is_not_damaging(self, movedex):
        entry = movedex.get("swordsdance")
        assert entry.is_damaging is False
        assert entry.has_variable_power is False

    def test_variable_power_move_is_damaging(self, movedex):
        """자이로볼은 데미지를 주지만 위력이 스피드로 정해진다."""
        entry = movedex.get("gyroball")
        assert entry.is_damaging is True
        assert entry.power is None
        assert entry.has_variable_power is True

    def test_fixed_power_move_is_not_variable(self, movedex):
        assert movedex.get("earthquake").has_variable_power is False

    def test_never_misses(self, movedex):
        assert movedex.get("aerialace").never_misses is True
        assert movedex.get("earthquake").never_misses is False


class TestToMoveSpec:
    def test_builds_a_spec(self, movedex):
        spec = movedex.get("earthquake").to_move_spec()
        assert spec == MoveSpec(
            power=100, move_type=PokemonType.GROUND, category=MoveCategory.PHYSICAL
        )

    def test_status_move_is_refused(self, movedex):
        with pytest.raises(MoveIsNotDamaging):
            movedex.get("swordsdance").to_move_spec()

    def test_variable_power_without_a_value_is_refused(self, movedex):
        """0 으로 때우지 않는다. 모르는 건 모른다고 실패해야 한다."""
        with pytest.raises(MoveHasNoFixedPower):
            movedex.get("gyroball").to_move_spec()

    def test_variable_power_accepts_a_supplied_value(self, movedex):
        spec = movedex.get("gyroball").to_move_spec(power=120)
        assert spec.power == 120
        assert spec.move_type is PokemonType.STEEL

    def test_supplied_power_overrides_a_fixed_one(self, movedex):
        """기술머신·특성으로 위력이 바뀌는 경우를 호출자가 반영할 수 있어야 한다."""
        assert movedex.get("earthquake").to_move_spec(power=50).power == 50


class TestConstruction:
    def test_rejects_unknown_type(self):
        with pytest.raises(UnknownPokemonType):
            MoveDex.from_mapping({"x": _entry(type="mystery")})

    def test_rejects_unknown_category(self):
        with pytest.raises(UnknownMoveCategory):
            MoveDex.from_mapping({"x": _entry(category="PHYSICAL")})

    def test_rejects_missing_field(self):
        record = _entry()
        del record["pp"]
        with pytest.raises(KeyError):
            MoveDex.from_mapping({"x": record})

    def test_rejects_non_integer_power(self):
        with pytest.raises(TypeError):
            MoveDex.from_mapping({"x": _entry(power="40")})

    def test_rejects_boolean_power(self):
        """True 는 파이썬에서 1 로 통과해버린다. 막아야 한다."""
        with pytest.raises(TypeError):
            MoveDex.from_mapping({"x": _entry(power=True)})

    def test_keeps_null_power_distinct_from_zero(self):
        dex = MoveDex.from_mapping({"x": _entry(power=None), "y": _entry(power=0, slug="y")})
        assert dex.get("x").power is None
        assert dex.get("y").power == 0

    def test_flags_become_a_frozenset(self):
        entry = MoveDex.from_mapping({"x": _entry(flags=["punch", "punch"])}).get("x")
        assert entry.flags == frozenset({"punch"})
