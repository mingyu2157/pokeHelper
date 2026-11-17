"""복구 리허설 — 백업이 실제로 복구되는지 검증한다 (ADR-014).

★ 복구가 검증되지 않은 백업은 백업이 아니다 ★

`scripts/backup.sh` 와 `scripts/restore.sh` 를 subprocess 로 실제 실행한다.
`pg_dump` 를 테스트가 직접 부르면 PostgreSQL 이 동작한다는 것만 확인된다.

검증을 네 층으로 나눈 이유는, 층마다 **놓치는 것이 다르기** 때문이다.

| 층 | 단정 | 이 층이 없으면 놓치는 것 |
|---|---|---|
| 1 | 행 수 | — (기본) |
| 2 | 내용 체크섬 | 행 수는 같고 값이 깨진 복구 |
| 3 | 제약·인덱스 | 데이터는 맞지만 **무결성 장치가 없는** DB |
| 4 | 시퀀스 | 복구 직후 첫 INSERT 가 PK 충돌 |

3층이 이 프로젝트의 핵심이다. Phase 2-4 의 핵심 장치가 부분 유니크 인덱스
(`uq_meta_snapshot_active`)인데, 복구 후 그게 없으면 "활성 스냅샷은 (레귤레이션,
포맷)당 하나"라는 불변식이 조용히 사라진다. 데이터는 완벽한 상태로.

선행 조건: docker compose --profile rehearsal up -d db-rehearsal
"""

from __future__ import annotations

import re
import subprocess

import pytest

from tests.rehearsal.conftest import RehearsalResult, psql

pytestmark = pytest.mark.rehearsal


class TestDestructionActuallyHappened:
    """★ 테스트 자신의 오라클 검사 — 파괴가 일어나지 않았다면 이후 단정이 전부 공짜다.

    표준 기법명을 붙이지 않는다. 확인하는 것은 "이 리허설이 실제로 무언가를
    복구했다"는 전제 그 자체다. 이 단정이 없으면 DROP 이 조용히 실패해도
    나머지 테스트가 모두 초록으로 통과한다 — 아무것도 검증하지 않은 채로.
    """

    def test_all_tables_were_dropped_before_restore(self, rehearsal: RehearsalResult):
        assert rehearsal.tables_after_destroy == 0

    def test_there_was_something_to_lose(self, rehearsal: RehearsalResult):
        """파괴 전에 테이블과 행이 실제로 있었다. 빈 DB 를 복구해도 전후는 같다."""
        assert len(rehearsal.before.row_counts) >= 9
        assert sum(rehearsal.before.row_counts.values()) > 0


class TestRowCounts:
    """메타모픽 관계 단정 — `restore(backup(D)) ≡ D` 의 행 수 층."""

    def test_table_set_is_identical(self, rehearsal: RehearsalResult):
        assert set(rehearsal.after.row_counts) == set(rehearsal.before.row_counts)

    def test_every_table_has_the_same_row_count(self, rehearsal: RehearsalResult):
        assert rehearsal.after.row_counts == rehearsal.before.row_counts


class TestContentChecksums:
    """메타모픽 관계 단정 — 같은 관계의 **내용** 층.

    이 검증이 막는 것: 행 수는 맞는데 값이 바뀐 복구. 행만 세면 통과한다.
    """

    def test_table_contents_match(self, rehearsal: RehearsalResult):
        mismatched = {
            table: (rehearsal.before.checksums[table], checksum)
            for table, checksum in rehearsal.after.checksums.items()
            if rehearsal.before.checksums.get(table) != checksum
        }
        assert mismatched == {}

    def test_seeded_rows_are_readable_after_restore(self, rehearsal: RehearsalResult):
        """체크섬이 같아도 실제로 읽히는지 한 번 확인한다."""
        out = psql(
            "SELECT name_ko FROM pokemon WHERE showdown_id = 'garchomp'",
            env=None,
        ).stdout.strip()
        assert out == "한카리아스"


_CAST = re.compile(r"::[a-z ]+(\[\])?")


def _same_check(definition: str) -> str:
    """제약 정의를 **렌더링 차이를 걷어낸** 비교용 형태로 바꾼다.

    ★ 왜 문자열 동일성이 오라클이 아닌가 ★

    `pg_get_constraintdef` 은 저장된 문자열이 아니라 **파스 트리를 다시 렌더링한
    결과**다. pg_dump → pg_restore 를 거치면 의미가 같아도 렌더링이 달라진다.
    실측된 예 (ck_moves_category):

        복구 전: ANY ((ARRAY['physical'::character varying, ...])::text[])
        복구 후: ANY (ARRAY[('physical'::character varying)::text, ...])

    캐스트를 배열 전체에 걸었는지 원소마다 걸었는지의 차이이고, 같은 검사다.

    그래서 이 층이 보는 것은 "렌더링이 같다"가 아니라 **"같은 검사가 남아 있다"**
    다. 값 목록이나 비교 대상이 바뀌면 정규화 후에도 달라지므로 실제 변경은
    여전히 잡힌다. 제약이 **집행되는지**는 TestConstraintsStillEnforce 가
    따로, 위반을 실제로 넣어서 단정한다.
    """
    return re.sub(r"[\s()]", "", _CAST.sub("", definition))


class TestSchemaObjectsSurvive:
    """차등 단정 — 카탈로그 정의를 전후로 비교한다 (3층).

    행 비교로는 절대 드러나지 않는 층이다. 인덱스가 사라진 DB 도 SELECT 는
    전부 정상으로 답한다.
    """

    def test_index_definitions_are_identical(self, rehearsal: RehearsalResult):
        """인덱스 정의는 왕복해도 문자 그대로 같다. 여기는 정규화하지 않는다."""
        assert rehearsal.after.indexes == rehearsal.before.indexes

    def test_constraint_names_are_identical(self, rehearsal: RehearsalResult):
        """★ 엄격한 쪽. 제약이 하나라도 사라지거나 생기면 여기서 깨진다."""
        assert set(rehearsal.after.constraints) == set(rehearsal.before.constraints)

    def test_constraint_checks_survive_rerendering(self, rehearsal: RehearsalResult):
        before = {name: _same_check(d) for name, d in rehearsal.before.constraints.items()}
        after = {name: _same_check(d) for name, d in rehearsal.after.constraints.items()}
        assert after == before

    def test_the_partial_unique_index_is_back(self, rehearsal: RehearsalResult):
        """★ Phase 2-4 의 핵심 장치. 이름과 조건절(WHERE is_active)까지 확인한다."""
        definition = rehearsal.after.indexes.get("uq_meta_snapshot_active", "")
        assert "UNIQUE" in definition
        assert "is_active" in definition


class TestConstraintsStillEnforce:
    """부정 테스트 — 제약이 **존재하는 것**과 **거부하는 것**은 별개다 (3층).

    카탈로그에 정의가 있어도 인덱스가 무효(INVALID) 상태면 막지 못한다.
    그래서 정의 비교에 그치지 않고 **실제로 위반을 넣어 거부되는지** 본다.
    ADR-013 의 교훈과 같은 구조다 — 선언과 집행은 다른 것이다.

    ★ 모든 테스트가 `rehearsal` 픽스처를 요구한다 ★
    요구하지 않으면 **복구 전 빈 DB 에 대고 돌아간다.** 그러면 INSERT 가
    "relation does not exist" 로 실패하고, 제약이 거부한 것으로 오인된다.
    거부 이유를 stderr 에서 제약 이름으로 확인하는 것도 그래서다.
    """

    @staticmethod
    def _expect_rejection(sql: str, constraint: str) -> None:
        result = psql(sql, check=False)
        assert result.returncode != 0, f"{constraint} 가 위반을 통과시켰다"
        assert constraint in result.stderr, result.stderr

    def test_second_active_snapshot_is_rejected(self, rehearsal: RehearsalResult):
        """같은 (레귤레이션, 포맷)에 활성 스냅샷 두 개를 넣으면 거부된다."""
        self._expect_rejection(
            """
            INSERT INTO meta_snapshots
                (regulation_id, source, format, collected_at, expires_at, is_active)
            SELECT r.id, 'championsbattledata', 'Singles',
                   '2026-11-15 00:00:00+00', '2026-12-15 00:00:00+00', TRUE
              FROM regulations r WHERE r.code = 'REH-A';
            """,
            "uq_meta_snapshot_active",
        )

    def test_impossible_base_hp_is_rejected(self, rehearsal: RehearsalResult):
        self._expect_rejection(
            """
            INSERT INTO pokemon (
                showdown_id, name_en, name_ko, display_name, form_kind,
                type1, type2, base_hp, base_atk, base_def, base_spa, base_spd, base_spe,
                ability_ids
            ) VALUES ('reh-bad-hp', 'Bad', '잘못된', '잘못된', 'Base',
                      'fire', NULL, 0, 50, 50, 50, 50, 50, '[]');
            """,
            "ck_pokemon_base_hp",
        )

    def test_duplicate_types_are_rejected(self, rehearsal: RehearsalResult):
        self._expect_rejection(
            """
            INSERT INTO pokemon (
                showdown_id, name_en, name_ko, display_name, form_kind,
                type1, type2, base_hp, base_atk, base_def, base_spa, base_spd, base_spe,
                ability_ids
            ) VALUES ('reh-bad-type', 'Bad', '잘못된', '잘못된', 'Base',
                      'fire', 'fire', 50, 50, 50, 50, 50, 50, '[]');
            """,
            "ck_pokemon_types_differ",
        )

    def test_expiry_before_collection_is_rejected(self, rehearsal: RehearsalResult):
        self._expect_rejection(
            """
            INSERT INTO meta_snapshots
                (regulation_id, source, format, collected_at, expires_at, is_active)
            SELECT r.id, 'championsbattledata', 'Singles',
                   '2026-11-01 00:00:00+00', '2026-10-01 00:00:00+00', FALSE
              FROM regulations r WHERE r.code = 'REH-B';
            """,
            "ck_snapshot_expiry_after_collection",
        )


class TestSequencesSurvive:
    """4층 — 복구 직후 첫 INSERT 가 PK 충돌하지 않는가.

    이 검증이 막는 것: 데이터만 복구되고 시퀀스가 1 로 되돌아간 상태.
    SELECT 는 전부 정상이고 **쓰기 시작할 때** 터진다. 운영에서 가장 늦게
    발견되는 종류의 복구 실패다.
    """

    def test_sequence_positions_are_identical(self, rehearsal: RehearsalResult):
        assert rehearsal.after.sequences == rehearsal.before.sequences

    def test_insert_after_restore_does_not_collide(self, rehearsal: RehearsalResult):
        """실제로 넣어본다. 상태를 더럽히지 않게 롤백한다."""
        result = psql(
            """
            BEGIN;
            INSERT INTO regulations (code, name, started_at, rules)
            VALUES ('REH-SEQ', '시퀀스 확인', '2026-11-17', '{}');
            ROLLBACK;
            """,
            check=False,
        )
        assert result.returncode == 0, result.stderr


class TestDumpArtifact:
    """덤프 파일 자체가 스키마 객체를 담고 있는가.

    이 검증이 막는 것: 데이터만 담은 덤프(`--data-only`)로 바뀌는 변경.
    그러면 복구는 "성공"하지만 제약이 없는 DB 가 된다.
    """

    def test_dump_exists_and_is_not_empty(self, rehearsal: RehearsalResult):
        assert rehearsal.dump_path.is_file()
        assert rehearsal.dump_path.stat().st_size > 0

    def test_dump_table_of_contents_includes_schema_objects(self, rehearsal: RehearsalResult):
        listing = subprocess.run(
            ["pg_restore", "--list", str(rehearsal.dump_path)],
            capture_output=True,
            text=True,
            check=True,
            timeout=60,
        ).stdout
        assert "INDEX" in listing
        assert "CONSTRAINT" in listing
        assert "TABLE DATA" in listing
