"""백업 스크립트 자체의 계약 — 보존 기간과 실패 경로 (ADR-014).

앞 파일(test_restore_rehearsal.py)이 "복구가 되는가"를 보고, 이 파일은
**"백업 산출물이 믿을 만한가"** 를 본다. 둘은 다른 질문이다. 복구가 되는
덤프를 만들면서도 실패한 백업을 성공처럼 남길 수 있다.

선행 조건: docker compose --profile rehearsal up -d db-rehearsal
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from tests.rehearsal.conftest import BACKUP_SCRIPT, run_script

pytestmark = pytest.mark.rehearsal

RETENTION_DAYS = 7
RETENTION_MINUTES = RETENTION_DAYS * 24 * 60


def _age(path: Path, minutes: float) -> None:
    """파일의 수정 시각을 과거로 민다."""
    past = time.time() - minutes * 60
    os.utime(path, (past, past))


def _dumps(directory: Path) -> set[str]:
    return {p.name for p in directory.iterdir()}


def _backup(env: dict[str, str], directory: Path, **overrides: str):
    return run_script(
        BACKUP_SCRIPT,
        env={
            **env,
            "BACKUP_DIR": str(directory),
            "BACKUP_RETENTION_DAYS": str(RETENTION_DAYS),
            **overrides,
        },
        check=False,
    )


class TestBackupContract:
    """스크립트가 호출자에게 약속하는 것.

    이 검증이 막는 것: 출력 형식이 바뀌어 리허설 fixture 와 운영 자동화가
    조용히 깨지는 것. 경로를 표준출력 한 줄로 주는 게 계약이다.
    """

    def test_prints_the_created_path_on_stdout(self, rehearsal_env, tmp_path: Path):
        result = _backup(rehearsal_env, tmp_path)
        assert result.returncode == 0, result.stderr
        created = Path(result.stdout.strip())
        assert created.is_file()
        assert created.parent == tmp_path
        assert created.suffix == ".dump"

    def test_filename_carries_a_utc_timestamp(self, rehearsal_env, tmp_path: Path):
        """파일명으로 정렬하면 시간순이 되어야 한다. 로컬 시간대로 찍으면 깨진다."""
        result = _backup(rehearsal_env, tmp_path)
        name = Path(result.stdout.strip()).name
        assert name.endswith("Z.dump")
        stamp = name.rsplit("-", 1)[-1].removesuffix(".dump")
        assert len(stamp) == len("20261117T000000Z")
        assert stamp[8] == "T"

    def test_no_partial_file_is_left_behind(self, rehearsal_env, tmp_path: Path):
        _backup(rehearsal_env, tmp_path)
        assert not list(tmp_path.glob("*.partial"))


class TestRetentionBoundary:
    """경계값 분석 — 보존 기간의 유효/무효 경계를 양쪽에서 집는다.

    ★ 정확히 N 분 경계는 단정하지 않는다 ★
    `find -mmin` 의 해상도가 분 단위이고, mtime 을 설정한 뒤 find 가 실행될
    때까지의 지연이 그 해상도보다 작지 않다. 즉 "정확히 7일"은 이 도구로
    재현 가능하게 만들 수 없다. 그래서 경계 ±5분으로 집고, 그 한계를 여기
    적어둔다. 재현 불가능한 단정을 넣으면 깜빡이는 테스트가 된다.
    """

    def test_a_backup_just_under_the_limit_survives(self, rehearsal_env, tmp_path: Path):
        old = tmp_path / "champs-20260101T000000Z.dump"
        old.write_bytes(b"stale but within retention")
        _age(old, RETENTION_MINUTES - 5)

        _backup(rehearsal_env, tmp_path)
        assert old.name in _dumps(tmp_path)

    def test_a_backup_just_over_the_limit_is_deleted(self, rehearsal_env, tmp_path: Path):
        old = tmp_path / "champs-20250101T000000Z.dump"
        old.write_bytes(b"beyond retention")
        _age(old, RETENTION_MINUTES + 5)

        _backup(rehearsal_env, tmp_path)
        assert old.name not in _dumps(tmp_path)

    def test_the_backup_just_created_is_never_deleted(self, rehearsal_env, tmp_path: Path):
        """정리가 방금 만든 백업을 지우면 백업이 하나도 남지 않는다."""
        result = _backup(rehearsal_env, tmp_path)
        created = Path(result.stdout.strip())
        assert created.is_file()

    def test_stale_partial_files_are_cleaned_up(self, rehearsal_env, tmp_path: Path):
        """중단된 덤프의 잔해도 보존 기간을 넘기면 지운다. 쌓이면 디스크를 먹는다."""
        junk = tmp_path / "champs-20250101T000000Z.dump.partial"
        junk.write_bytes(b"interrupted")
        _age(junk, RETENTION_MINUTES + 5)

        _backup(rehearsal_env, tmp_path)
        assert junk.name not in _dumps(tmp_path)

    def test_retention_zero_keeps_only_the_new_backup(self, rehearsal_env, tmp_path: Path):
        """0일이면 즉시 만료다. 방금 만든 것만 남는다 (경계의 아래쪽 끝)."""
        old = tmp_path / "champs-20260101T000000Z.dump"
        old.write_bytes(b"older than zero days")
        _age(old, 10)

        result = _backup(rehearsal_env, tmp_path, BACKUP_RETENTION_DAYS="0")
        assert result.returncode == 0, result.stderr
        assert old.name not in _dumps(tmp_path)
        assert Path(result.stdout.strip()).is_file()


class TestFailurePaths:
    """오류 추정 — 실패한 백업이 성공처럼 보이는 것이 최악의 실패 모드다.

    이 검증이 막는 것: 0 바이트 또는 끊긴 덤프가 보존 디렉토리에 남아
    "백업 있음"으로 오인되는 것. 정작 복구가 필요한 날에 알게 된다.
    """

    def test_unreachable_database_leaves_no_artifact(self, rehearsal_env, tmp_path: Path):
        result = _backup(rehearsal_env, tmp_path, PGHOST="db-rehearsal-nonexistent-host")
        assert result.returncode != 0
        assert _dumps(tmp_path) == set(), "실패했는데 파일이 남았다"

    def test_invalid_retention_is_rejected_before_touching_the_database(
        self, rehearsal_env, tmp_path: Path
    ):
        """숫자가 아닌 보존 기간으로 돌면 `find -mmin +abc` 가 되어 정리가 조용히
        실패하거나 엉뚱한 파일을 지운다. 덤프를 만들기 전에 막는다."""
        result = _backup(rehearsal_env, tmp_path, BACKUP_RETENTION_DAYS="abc")
        assert result.returncode != 0
        assert _dumps(tmp_path) == set()

    def test_restore_refuses_a_corrupt_dump(self, rehearsal_env, tmp_path: Path):
        """손상된 덤프로 복구를 걸면, 기존 객체를 지우기 **전에** 멈춘다.

        `--clean` 이 먼저 돌면 지운 뒤 실패해서 빈 DB 가 남는다.
        """
        from tests.rehearsal.conftest import RESTORE_SCRIPT, scalar

        corrupt = tmp_path / "corrupt.dump"
        corrupt.write_bytes(b"this is not a pg_dump archive")

        before = scalar("SELECT count(*) FROM pg_tables WHERE schemaname='public'")
        result = run_script(RESTORE_SCRIPT, str(corrupt), env=rehearsal_env, check=False)
        after = scalar("SELECT count(*) FROM pg_tables WHERE schemaname='public'")

        assert result.returncode != 0
        assert before == after, "손상된 덤프가 기존 스키마를 건드렸다"
