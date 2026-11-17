"""운영 스크립트의 접속 기본값 — 문서에 적힌 짧은 명령이 실제로 도는가 (ADR-015).

★ 이 파일이 존재하는 이유 ★

리허설 29개와 권한 테스트 21개가 전부 초록인데도 `docs/RUNBOOK.md` 의
`docker compose exec backend bash scripts/init_roles.sh` 는 동작하지 않았다.
원인은 테스트가 **환경변수를 전부 명시 주입**했다는 것이다. 검증한 명령과
문서에 적은 명령이 달랐고, 그 차이를 아무 테스트도 보지 않았다
(JOURNAL 2026-11-17).

그래서 여기서는 **계정을 일부러 알려주지 않는다.** 스크립트가 스스로 올바른
계정을 고르는지 본다 — 백업은 읽기 전용, 복구는 소유자, 역할 생성은 부트스트랩.

선행 조건: docker compose --profile rehearsal up -d db-rehearsal
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.rehearsal.conftest import (
    BACKUP_SCRIPT,
    REPO_ROOT,
    RESTORE_SCRIPT,
    bare_env,
    run_script,
)

pytestmark = pytest.mark.rehearsal

INIT_ROLES_SCRIPT = REPO_ROOT / "scripts" / "init_roles.sh"


class TestConnectionDefaults:
    """문서에 적힌 명령을 그대로 실행한다 (교과서 기법이 아니다).

    검증하는 것은 두 겹이다.
      1. 환경변수 없이도 **동작한다** — 문서가 거짓이 아니다
      2. 그때 고르는 계정이 **최소 권한 계정이다** — 편의가 슈퍼유저로 돌지 않는다

    2번이 없으면 1번을 부트스트랩 슈퍼유저 기본값으로 쉽게 통과시킬 수 있고,
    그러면 ADR-015 의 계정 분리가 평소 경로에서 무력화된다.
    """

    def test_backup_runs_as_the_read_only_account(self, tmp_path: Path):
        """백업에 쓰기 권한은 필요 없다. 기본값이 champs_read 여야 한다."""
        result = run_script(
            BACKUP_SCRIPT,
            env=bare_env(BACKUP_DIR=str(tmp_path), BACKUP_RETENTION_DAYS="7"),
            check=False,
        )

        assert result.returncode == 0, result.stderr
        assert "user=champs_read" in result.stderr, result.stderr
        assert Path(result.stdout.strip()).is_file()

    def test_restore_runs_as_the_owning_account(self, tmp_path: Path):
        """복구는 `--clean` 으로 기존 객체를 DROP 하므로 **소유자**여야 한다.

        champs_app 이 기본값이면 복구가 중간에 멈춘다. 여기서는 접속 직전까지만
        확인한다 — 실제 복구는 앞 파일이 전체 주기로 검증하고, 이 테스트가 또
        복구를 걸면 다른 테스트가 보는 상태를 흔든다.
        """
        dump = run_script(
            BACKUP_SCRIPT,
            env=bare_env(BACKUP_DIR=str(tmp_path), BACKUP_RETENTION_DAYS="7"),
        ).stdout.strip()

        result = run_script(
            RESTORE_SCRIPT,
            dump,
            env=bare_env(PGHOST="db-rehearsal-nonexistent-host"),
            check=False,
        )

        assert "user=champs_migrate" in result.stderr, result.stderr
        assert result.returncode != 0, "닿을 수 없는 호스트인데 성공했다"

    def test_init_roles_falls_back_to_the_bootstrap_account(self):
        """역할 생성은 CREATEROLE 이 필요해서 분리 계정으로는 할 수 없다.

        compose 가 주는 `POSTGRES_*` 에서 부트스트랩 접속 정보를 끌어와야 한다.
        멱등하므로 리허설 DB 를 반복 실행해도 상태가 같다.
        """
        result = run_script(INIT_ROLES_SCRIPT, env=bare_env(), check=False)

        assert result.returncode == 0, result.stderr
        assert "user=champs" in result.stderr, result.stderr
        assert "user=champs_" not in result.stderr, "부트스트랩이 아닌 계정으로 붙었다"

    def test_init_roles_stops_when_the_bootstrap_account_is_unknown(self):
        """부정 테스트 — 접속 정보를 못 찾으면 psql 을 부르기 전에 멈춘다.

        비워두면 psql 이 OS 사용자명으로 붙으려 하다가 `role "root" does not
        exist` 같은 엉뚱한 오류를 낸다. 원인이 '변수 누락'임을 말해주지 않는다.
        """
        env = bare_env()
        env.pop("POSTGRES_USER", None)

        result = run_script(INIT_ROLES_SCRIPT, env=env, check=False)

        assert result.returncode != 0
        assert "PGUSER 를 알 수 없다" in result.stderr, result.stderr
