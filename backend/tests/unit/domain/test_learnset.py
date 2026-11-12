"""learnset 조회 테스트.

데이터는 `backend/data/learnsets.json` (championsbattledata 인덱스에서 생성).
domain 은 파일을 읽지 않으므로 테스트가 읽어 주입한다 (ADR-007).
"""

import json
from pathlib import Path

import pytest

from app.domain.exceptions import UnknownPokemon
from app.domain.learnset import Learnset

BACKEND_DIR = Path(__file__).resolve().parents[3]
LEARNSET_PATH = BACKEND_DIR / "data" / "learnsets.json"
MOVES_PATH = BACKEND_DIR / "data" / "moves.json"
POKEMON_PATH = BACKEND_DIR / "data" / "pokemon.json"


def _read(path: Path):
    with path.open(encoding="utf-8") as file:
        return json.load(file)


@pytest.fixture(scope="module")
def raw_data():
    return _read(LEARNSET_PATH)


@pytest.fixture(scope="module")
def learnset(raw_data):
    return Learnset.from_mapping(raw_data["learnsets"])


class TestDataFile:
    def test_counts_agree(self, raw_data):
        meta = raw_data["_meta"]
        assert meta["pokemon"] == len(raw_data["learnsets"])
        assert meta["pairs"] == sum(len(v) for v in raw_data["learnsets"].values())

    def test_covers_every_pokemon(self, raw_data):
        """★ 한 종이라도 빠지면 파티 빌더가 그 포켓몬을 검증하지 못한다."""
        seeded = set(_read(POKEMON_PATH)["pokemon"])
        missing = sorted(seeded - set(raw_data["learnsets"]))
        assert not missing, f"learnset 이 없는 포켓몬: {missing[:10]}"

    def test_every_move_resolves(self, raw_data):
        """★ 해석 안 되는 기술이 있으면 '못 배운다'는 틀린 사실이 들어간다."""
        known = set(_read(MOVES_PATH)["moves"])
        unresolved = {
            move_id
            for move_ids in raw_data["learnsets"].values()
            for move_id in move_ids
            if move_id not in known
        }
        assert not unresolved, f"기술표에 없는 기술: {sorted(unresolved)[:10]}"

    def test_no_duplicates(self, raw_data):
        for showdown_id, move_ids in raw_data["learnsets"].items():
            assert len(move_ids) == len(set(move_ids)), showdown_id

    def test_lists_are_sorted(self, raw_data):
        for showdown_id, move_ids in raw_data["learnsets"].items():
            assert move_ids == sorted(move_ids), showdown_id


class TestRecoveredMoveIsPresent:
    """★ 전기쇼크는 이 learnset 때문에 기술표에 들어왔다.

    op.gg 목록 515개에 없던 기술인데 로토무가 배운다. 기술표를 본가 데이터나
    목록만으로 다시 만들면 여기서 깨진다 (JOURNAL 2026-11-13).
    """

    @pytest.mark.parametrize(
        "showdown_id", ["rotomheat", "rotomwash", "rotomfrost", "rotomfan", "rotommow"]
    )
    def test_rotom_forms_learn_thunder_shock(self, learnset, showdown_id):
        assert learnset.can_learn(showdown_id, "thundershock")


class TestLookup:
    def test_known_move(self, learnset):
        assert learnset.can_learn("garchomp", "earthquake") is True

    def test_unknown_move_is_false(self, learnset):
        """기술은 '모르는 것'이 아니라 '안 배우는 것'이다 — False 가 맞다."""
        assert learnset.can_learn("garchomp", "hydropump") is False

    def test_unknown_pokemon_raises(self, learnset):
        """★ '못 배운다'와 '모른다'를 섞지 않는다."""
        with pytest.raises(UnknownPokemon):
            learnset.can_learn("mysterymon", "earthquake")

    def test_moves_of_returns_a_set(self, learnset):
        moves = learnset.moves_of("garchomp")
        assert isinstance(moves, frozenset)
        assert "earthquake" in moves
        assert len(moves) > 10

    def test_ids_are_sorted(self, learnset):
        assert list(learnset.ids()) == sorted(learnset.ids())

    def test_pair_count_matches_data(self, learnset, raw_data):
        assert learnset.pair_count() == raw_data["_meta"]["pairs"]


class TestUnlearnable:
    """파티 검증에서 쓰는 경로. 어느 기술이 문제인지 알려줘야 고칠 수 있다."""

    def test_returns_only_the_bad_ones(self, learnset):
        result = learnset.unlearnable("garchomp", ("earthquake", "hydropump", "outrage", "psychic"))
        assert "hydropump" in result
        assert "earthquake" not in result
        assert "outrage" not in result

    def test_empty_when_all_learnable(self, learnset):
        assert learnset.unlearnable("garchomp", ("earthquake", "outrage")) == ()

    def test_preserves_input_order(self, learnset):
        result = learnset.unlearnable("garchomp", ("hydropump", "psychic"))
        assert result == ("hydropump", "psychic")

    def test_unknown_pokemon_raises(self, learnset):
        with pytest.raises(UnknownPokemon):
            learnset.unlearnable("mysterymon", ("earthquake",))


class TestConstruction:
    def test_builds_from_plain_lists(self):
        learnset = Learnset.from_mapping({"x": ["tackle", "growl"]})
        assert learnset.can_learn("x", "tackle")
        assert learnset.moves_of("x") == frozenset({"tackle", "growl"})

    def test_empty_list_is_allowed_but_learns_nothing(self):
        """빈 목록은 '모른다'가 아니라 '하나도 못 배운다'다. 예외가 아니다."""
        learnset = Learnset.from_mapping({"x": []})
        assert learnset.moves_of("x") == frozenset()
        assert learnset.can_learn("x", "tackle") is False
