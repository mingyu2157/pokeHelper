"""복구 비교 로직의 네 층이 **각각** 무엇을 잡는지 확인한다.

★ 왜 단위 테스트가 따로 필요한가 ★

`tests/rehearsal/` 은 실제 DB 로 백업→파괴→복구를 돌려 이 비교를 **쓴다.** 그런데
리허설은 "성공한 복구"만 만들어 낸다 — 비교가 **실패를 잡는지는** 거기서 증명되지
않는다. 네 층 중 하나가 아무것도 안 보고 있어도 리허설은 초록이다.

그래서 **망가진 복구를 손으로 만들어** 층마다 하나씩 들이민다. 복구는 실패해야
할 때 실패해야 쓸모가 있다.

★ 층을 왜 넷으로 나눴는지도 여기서 드러난다 ★

| 층 | 이 층만 잡는 것 |
|---|---|
| 1 행 수 | 통째로 빠진 테이블 |
| 2 체크섬 | **행 수는 같고 값이 깨진** 복구 |
| 3 제약·인덱스 | 데이터는 맞지만 **무결성 장치가 없는** DB |
| 4 시퀀스 | 복구 직후 첫 INSERT 가 **PK 충돌** |

2·3·4 는 전부 "행을 세는 비교"가 통과시키는 것들이다. 3번이 특히 위험하다 —
**그 순간에는 아무 증상이 없고** 나중에 잘못된 데이터가 들어올 때 드러난다.
"""

from __future__ import annotations

import pytest

from scripts.db_state import (
    DatabaseState,
    compare,
    env_from_dsn,
    sequence_advances,
)


def state(**overrides: dict[str, object]) -> DatabaseState:
    """정상 복구 상태 하나를 만들고, 넘긴 층만 갈아끼운다."""
    base: dict[str, dict[str, object]] = {
        "row_counts": {"pokemon": 263, "moves": 516},
        "checksums": {"pokemon": "aaa", "moves": "bbb"},
        "sequences": {"regulations_id_seq": "7"},
        "indexes": {"ix_pokemon_name_ko": "CREATE INDEX ix_pokemon_name_ko ON pokemon (name_ko)"},
        "constraints": {"ck_pokemon_base_hp": "CHECK (base_hp > 0)"},
    }
    base.update(overrides)
    return DatabaseState(**base)  # type: ignore[arg-type]


class TestAPerfectRestorePasses:
    """전제 선행 단정 — 같은 상태가 '다르다'로 나오면 나머지 검사가 전부 무의미하다."""

    def test_identical_states_report_nothing(self) -> None:
        assert compare(state(), state()) == []

    def test_key_order_does_not_matter(self) -> None:
        """딕셔너리 순서가 달라도 같은 상태다. 순서에 민감하면 거짓 양성이 쏟아진다."""
        reordered = state(checksums={"moves": "bbb", "pokemon": "aaa"})
        assert compare(state(), reordered) == []


class TestLayerOneCatchesMissingRows:
    """1층 — 행 수. 통째로 빠진 테이블과 일부만 복구된 테이블."""

    def test_a_table_vanished(self) -> None:
        after = state(row_counts={"pokemon": 263}, checksums={"pokemon": "aaa"})
        differences = compare(state(), after)
        assert any("사라짐: moves" in line for line in differences)

    def test_partially_restored_table(self) -> None:
        after = state(row_counts={"pokemon": 263, "moves": 500})
        differences = compare(state(), after)
        assert any("1 행 수" in line and "moves" in line for line in differences)

    def test_an_unexpected_table_appeared(self) -> None:
        """★ '더 복구된 것'도 차이다. 복구본에 남은 임시 테이블을 잡는다."""
        after = state(row_counts={"pokemon": 263, "moves": 516, "leftover_tmp": 3})
        differences = compare(state(), after)
        assert any("생김: leftover_tmp" in line for line in differences)


class TestLayerTwoCatchesCorruptedValues:
    """★ 2층 — 체크섬. **행 수는 같은데 값이 깨진** 복구.

    1층만 있으면 이 경우가 통과한다. 그게 체크섬 층을 둔 이유다.
    """

    def test_same_row_count_but_different_content(self) -> None:
        after = state(checksums={"pokemon": "CORRUPTED", "moves": "bbb"})
        differences = compare(state(), after)
        assert any("2 내용 체크섬" in line and "pokemon" in line for line in differences)

    def test_row_counts_alone_would_have_missed_it(self) -> None:
        """★ 이 테스트가 2층의 존재 이유를 못 박는다."""
        after = state(checksums={"pokemon": "CORRUPTED", "moves": "bbb"})
        assert state().row_counts == after.row_counts
        assert compare(state(), after) != []


class TestLayerThreeCatchesMissingIntegrity:
    """★ 3층 — 제약·인덱스. 데이터는 맞는데 무결성 장치가 사라진 DB.

    ★ **그 순간에는 아무 증상이 없다.** 다음에 잘못된 데이터가 들어올 때 드러난다.
    """

    def test_a_check_constraint_disappeared(self) -> None:
        after = state(constraints={})
        differences = compare(state(), after)
        assert any("3 제약" in line and "사라짐" in line for line in differences)

    def test_a_constraint_definition_changed(self) -> None:
        """이름은 같은데 정의가 느슨해진 경우. 이름만 보면 놓친다."""
        after = state(constraints={"ck_pokemon_base_hp": "CHECK (base_hp >= 0)"})
        differences = compare(state(), after)
        assert any("3 제약" in line and "다름" in line for line in differences)

    def test_an_index_disappeared(self) -> None:
        after = state(indexes={})
        differences = compare(state(), after)
        assert any("3 인덱스" in line and "사라짐" in line for line in differences)

    def test_data_layers_would_have_passed(self) -> None:
        """★ 1·2층이 전부 통과하는데 3층만 잡는 경우."""
        after = state(constraints={})
        assert state().row_counts == after.row_counts
        assert state().checksums == after.checksums
        assert compare(state(), after) != []


class TestLayerFourCatchesSequenceDrift:
    """★ 4층 — 시퀀스. 복구 직후 **첫 INSERT 가 PK 충돌**하는 경우.

    행도 맞고 체크섬도 맞고 제약도 맞는데, 시퀀스만 뒤처져 있으면 복구는
    성공해 보이고 **다음 쓰기에서 터진다.**

    ★ 이 층만 정확한 일치가 아니라 **방향**으로 판정한다 (ADR-023).
      판정 규칙 자체는 `TestSequencesAreJudgedByDirection` 가 본다.
    """

    def test_sequence_is_behind(self) -> None:
        after = state(sequences={"regulations_id_seq": "1"})
        differences = compare(state(), after)
        assert any("4 시퀀스" in line for line in differences)

    def test_every_other_layer_would_have_passed(self) -> None:
        after = state(sequences={"regulations_id_seq": "1"})
        assert state().row_counts == after.row_counts
        assert state().checksums == after.checksums
        assert state().constraints == after.constraints
        assert compare(state(), after) != []

    def test_an_unused_sequence_is_not_a_difference(self) -> None:
        """양쪽 다 `<unused>` 면 같은 상태다. 거짓 양성을 막는다."""
        unused = state(sequences={"regulations_id_seq": "<unused>"})
        assert compare(unused, unused) == []


class TestSequencesAreJudgedByDirection:
    """★ 경계값 분석 — 4층의 판정 경계는 `복원본 == 원본` 이다 (ADR-023).

    양쪽에서 1 씩 집는다. 같으면 안전, 1 앞서면 안전, 1 뒤처지면 차이.
    **경계 자체(같음)가 안전 쪽에 포함**되는 것이 이 규칙의 핵심이다.

    왜 방향인가: 이 층이 막으려는 것은 "값이 다른 것"이 아니라 **PK 충돌**이다.
    앞선 시퀀스는 ID 를 건너뛸 뿐 충돌하지 않는다. 판정 기준을 목적과 일치시킨다.
    """

    @staticmethod
    def _sequences(value: str) -> dict[str, str]:
        return {"regulations_id_seq": value}

    def test_equal_is_safe(self) -> None:
        assert compare(state(), state()) == []

    def test_one_ahead_is_not_a_difference(self) -> None:
        after = state(sequences=self._sequences("8"))  # 원본 7
        assert compare(state(), after) == []

    def test_one_behind_is_a_difference(self) -> None:
        after = state(sequences=self._sequences("6"))  # 원본 7
        differences = compare(state(), after)
        assert any("뒤처짐" in line for line in differences)

    def test_far_ahead_is_still_not_a_difference(self) -> None:
        """★ 32 라는 수에 결합하지 않는다. PostgreSQL 내부 상수(SEQ_LOG_VALS)는
        바뀔 수 있고, 안전 조건은 '앞섰다' 하나뿐이다."""
        assert compare(state(), state(sequences=self._sequences("999999"))) == []

    def test_a_disappeared_sequence_is_still_a_difference(self) -> None:
        """방향 판정은 **양쪽에 다 있는** 키에만 적용된다."""
        differences = compare(state(), state(sequences={}))
        assert any("4 시퀀스" in line and "사라짐" in line for line in differences)

    def test_an_appeared_sequence_is_still_a_difference(self) -> None:
        differences = compare(state(sequences={}), state())
        assert any("4 시퀀스" in line and "생김" in line for line in differences)


class TestUnusedSequenceIsPositionZero:
    """동등 분할 — `<unused>` 는 숫자가 아닌 특수 클래스다. **0 으로 본다.**

    한 번도 쓰지 않은 시퀀스는 다음에 1 을 내준다. 그래서 원본이 7 인데
    복원본이 `<unused>` 면 **1 부터 다시 시작해 PK 충돌**이다 — 가장 위험한
    경우인데, 문자열 비교로는 그냥 "다름"이고 방향 비교로는 자칫 빠진다.
    """

    def test_used_to_unused_is_a_difference(self) -> None:
        after = state(sequences={"regulations_id_seq": "<unused>"})
        differences = compare(state(), after)
        assert any("뒤처짐" in line for line in differences)

    def test_unused_to_used_is_safe(self) -> None:
        before = state(sequences={"regulations_id_seq": "<unused>"})
        assert compare(before, state()) == []

    def test_unused_on_both_sides_is_safe(self) -> None:
        unused = state(sequences={"regulations_id_seq": "<unused>"})
        assert compare(unused, unused) == []


class TestUnreadableSequenceValueFailsClosed:
    """부정 테스트 / 오류 추정 — 판정할 수 없는 값은 **통과시키지 않는다.**

    `pg_sequences` 가 예상 밖 문자열을 돌려주면 방향을 알 수 없다. 조용히
    넘기면 뒤처짐을 놓치므로 차이로 올린다. fail-closed.
    """

    def test_unparseable_restored_value_is_reported(self) -> None:
        after = state(sequences={"regulations_id_seq": "???"})
        differences = compare(state(), after)
        assert any("읽을 수 없음" in line for line in differences)

    def test_unparseable_source_value_is_reported(self) -> None:
        before = state(sequences={"regulations_id_seq": "???"})
        differences = compare(before, state())
        assert any("읽을 수 없음" in line for line in differences)

    def test_an_unreadable_value_is_not_counted_as_an_advance(self) -> None:
        """참고 출력에도 올라가지 않는다 — 앞섰다고 말할 근거가 없다."""
        assert sequence_advances(state(), state(sequences={"regulations_id_seq": "???"})) == []


class TestAdvancesAreReportedWithoutFailing:
    """★ 규칙을 테스트가 집행한다 — "거짓 양성은 없애되 사실은 버리지 않는다".

    앞선 시퀀스는 차이가 아니지만, 정보를 숨기면 다음 사람이 같은 조사를
    처음부터 반복한다. 그래서 `compare` 에서는 빠지고 `sequence_advances` 에는
    남아야 한다. **두 경로가 동시에 성립하는지**를 한 테스트에서 단정한다.
    """

    def test_an_advance_is_absent_from_differences_and_present_in_notes(self) -> None:
        after = state(sequences={"regulations_id_seq": "39"})  # 원본 7
        assert compare(state(), after) == []
        notes = sequence_advances(state(), after)
        assert len(notes) == 1
        assert "regulations_id_seq" in notes[0]
        assert "+32" in notes[0]

    def test_a_regression_is_not_reported_as_an_advance(self) -> None:
        after = state(sequences={"regulations_id_seq": "1"})
        assert sequence_advances(state(), after) == []
        assert compare(state(), after) != []

    def test_identical_sequences_produce_no_notes(self) -> None:
        assert sequence_advances(state(), state()) == []


class TestTheProductionCaseThatCausedThisRule:
    """★ 골든(동결) 오라클 — 2026-11-26 RDS PITR 리허설의 **실측값**.

    이 두 줄이 ADR-023 을 쓰게 만든 원인이다. 운영에서 실제로 나온 값이므로
    기대값을 추측하지 않는다.

        meta_snapshots_id_seq  3   → 35   (+32)
        pokemon_meta_id_seq    150 → 166  (+16)

    둘 다 `0 ≤ 차 ≤ 32` 이고 둘 다 앞섰다. 규칙이 바뀌면 이 케이스가 먼저 깨진다.
    """

    MEASURED_BEFORE = {"meta_snapshots_id_seq": "3", "pokemon_meta_id_seq": "150"}
    MEASURED_AFTER = {"meta_snapshots_id_seq": "35", "pokemon_meta_id_seq": "166"}

    def test_the_measured_restore_now_passes(self) -> None:
        before = state(sequences=self.MEASURED_BEFORE)
        after = state(sequences=self.MEASURED_AFTER)
        assert compare(before, after) == []

    def test_both_advances_are_still_reported(self) -> None:
        notes = sequence_advances(
            state(sequences=self.MEASURED_BEFORE), state(sequences=self.MEASURED_AFTER)
        )
        assert len(notes) == 2
        assert any("+32" in note for note in notes)
        assert any("+16" in note for note in notes)


class TestEveryLayerIsActuallyWired:
    """전수 조합 — ★ 다섯 필드 **각각**이 차이를 만들어내는지 하나씩 본다.

    한 층이 비교에서 빠져 있어도 다른 층이 통과시키면 아무도 모른다.
    `LAYERS` 에서 한 줄을 지우는 변형이 이 테스트에 걸린다.
    """

    @pytest.mark.parametrize(
        ("field", "broken"),
        [
            ("row_counts", {"pokemon": 1, "moves": 516}),
            ("checksums", {"pokemon": "zzz", "moves": "bbb"}),
            # ★ 뒤처진 값이어야 한다. 앞선 값(999)은 ADR-023 이후 **정상**이므로
            #   이 테스트가 공짜로 통과한다. 층이 연결됐는지 보려면 불안전한
            #   방향으로 깨뜨려야 한다.
            ("sequences", {"regulations_id_seq": "1"}),
            ("indexes", {"ix_pokemon_name_ko": "CREATE INDEX ... (name_en)"}),
            ("constraints", {"ck_pokemon_base_hp": "CHECK (true)"}),
        ],
    )
    def test_breaking_one_field_is_reported(self, field: str, broken: dict[str, object]) -> None:
        differences = compare(state(), state(**{field: broken}))
        assert differences, f"{field} 를 깨뜨렸는데 비교가 아무것도 보고하지 않았다"


class TestDifferencesAreReadable:
    """규칙을 테스트가 집행한다 — 출력이 사람이 고칠 수 있는 형태여야 한다."""

    def test_a_difference_names_the_layer_and_the_key(self) -> None:
        differences = compare(state(), state(checksums={"pokemon": "zzz", "moves": "bbb"}))
        assert len(differences) == 1
        assert "체크섬" in differences[0]
        assert "pokemon" in differences[0]

    def test_direction_is_distinguishable(self) -> None:
        """★ '다르다'만으로는 덜 복구됐는지 더 복구됐는지 모른다."""
        missing = compare(state(), state(constraints={}))
        extra = compare(state(constraints={}), state())
        assert any("사라짐" in line for line in missing)
        assert any("생김" in line for line in extra)

    def test_all_differences_are_reported_not_just_the_first(self) -> None:
        after = state(row_counts={"pokemon": 1, "moves": 1}, constraints={})
        assert len(compare(state(), after)) >= 3


class TestEnvFromDsn:
    """★ 호스트만 갈아끼우는 것이 운영 리허설의 핵심이다.

    PITR 복원본은 **같은 DB 의 같은 계정**이고 엔드포인트만 다르다. 비밀번호를
    명령줄에 적지 않고 두 곳에 붙으려면 이 함수가 맞아야 한다.
    """

    DSN = "postgresql+asyncpg://champs_app:secret@old-host.rds.amazonaws.com:5432/champs"

    def test_it_maps_a_sqlalchemy_dsn_to_libpq_variables(self) -> None:
        env = env_from_dsn(self.DSN)
        assert env["PGHOST"] == "old-host.rds.amazonaws.com"
        assert env["PGPORT"] == "5432"
        assert env["PGUSER"] == "champs_app"
        assert env["PGPASSWORD"] == "secret"
        assert env["PGDATABASE"] == "champs"

    def test_host_override_changes_only_the_host(self) -> None:
        env = env_from_dsn(self.DSN, host_override="restored.rds.amazonaws.com")
        assert env["PGHOST"] == "restored.rds.amazonaws.com"
        assert env["PGUSER"] == "champs_app"
        assert env["PGPASSWORD"] == "secret"
        assert env["PGDATABASE"] == "champs"

    def test_asyncpg_driver_suffix_is_stripped(self) -> None:
        """`postgresql+asyncpg://` 를 그대로 urlparse 하면 스킴이 달라 깨진다."""
        assert env_from_dsn(self.DSN)["PGHOST"] == "old-host.rds.amazonaws.com"

    def test_default_port_when_absent(self) -> None:
        env = env_from_dsn("postgresql://u:p@h/db")
        assert env["PGPORT"] == "5432"

    def test_a_dsn_without_a_host_is_rejected(self) -> None:
        """★ 호스트가 없으면 libpq 가 **로컬 소켓**으로 붙는다. 조용히 엉뚱한
        DB 를 비교하느니 멈추는 게 낫다."""
        with pytest.raises(ValueError, match="호스트"):
            env_from_dsn("postgresql:///champs")

    def test_a_dsn_without_a_user_is_rejected(self) -> None:
        """사용자명이 없으면 libpq 가 OS 사용자명으로 붙는다 — 컨테이너에서는 root."""
        with pytest.raises(ValueError, match="사용자명"):
            env_from_dsn("postgresql://h:5432/champs")
