"""복구 리허설 공통 설비 (ADR-014).

★ 왜 psql 로 DB 와 대화하는가 ★

이 디렉토리는 SQLAlchemy 를 쓰지 않는다. 이유가 셋이다.

1. 리허설은 **테이블 전체를 지우고 되살린다.** ORM 은 스키마가 존재한다는 전제
   위에서 동작하므로, 파괴 직후의 상태를 다루기에 맞지 않는다.
2. 검증 대상이 행이 아니라 **제약·인덱스·시퀀스** 다. 이건 카탈로그(`pg_indexes`,
   `pg_constraint`, `pg_sequences`)를 직접 읽어야 보인다.
3. 장애 때 운영자가 실제로 쓰는 도구가 psql 이다. 같은 도구로 검증하면
   `docs/RUNBOOK.md` 의 확인 명령이 테스트로 뒷받침된다.

★ 네 층 비교는 `scripts/db_state.py` 에 있다 (2026-11-25 추출) ★

`DatabaseState`·`capture_state`·`psql`·`scalar`·`rows` 는 원래 이 파일에 있었다.
ADR-020 세부사항 5 가 "운영판은 **같은 4층을 비교**로 그대로 옮긴다"고 약속했는데,
운영 이미지는 `tests/` 를 복사하지 않는다. 그래서 `scripts/` 로 옮기고 여기서
import 한다 — 비교 기준이 환경마다 달라지지 않게 하려는 것이다.
**로컬 리허설 33건이 그대로 통과하는 것이 추출이 성공했다는 증거다.**

★ 왜 스크립트를 subprocess 로 실행하는가 ★

테스트가 `pg_dump` 를 직접 부르면 **PostgreSQL 이 동작한다**는 것만 확인되고,
`scripts/backup.sh` 에 대해서는 아무것도 증명하지 않는다. 검증 대상은 우리
백업 절차이므로 그 스크립트를 그대로 실행한다. ADR-013 에서 "상수가 1.0 인 것과
게이트가 그 값을 쓰는 것은 별개"였던 것과 같은 구조다.

선행 조건:
    docker compose --profile rehearsal up -d db-rehearsal

기본 실행에서 제외돼 있다 (`addopts` 의 `-m "not rehearsal"`). 프로필로 가려진
컨테이너가 필요해서 평소 `pytest` 를 초록으로 유지하려면 빼야 한다. 대신 명시적으로
불렀을 때 컨테이너가 없으면 **건너뛰지 않고 실패한다** — 조용히 건너뛰는 리허설은
리허설을 안 한 것과 같다.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import pytest

from scripts.db_state import DatabaseState
from scripts.db_state import capture_state as capture_state_impl
from scripts.db_state import psql as _psql
from scripts.db_state import rows as _rows
from scripts.db_state import scalar as _scalar
from scripts.db_state import table_names as _table_names

# /app/tests/rehearsal/conftest.py → /app
REPO_ROOT = Path(__file__).resolve().parents[2]
BACKUP_SCRIPT = REPO_ROOT / "scripts" / "backup.sh"
RESTORE_SCRIPT = REPO_ROOT / "scripts" / "restore.sh"

DEFAULT_REHEARSAL_URL = "postgresql+asyncpg://champs:devpassword@db-rehearsal:5432/champs"

START_HINT = "docker compose --profile rehearsal up -d db-rehearsal"

# 전부 지우는 SQL. 스키마는 남기고 테이블만 지운다 — `DROP SCHEMA public` 까지 하면
# pg_dump 가 스키마 생성문을 담고 있는지에 결과가 달라져 검증 대상이 흐려진다.
DESTROY_SQL = """
DO $$
DECLARE r record;
BEGIN
    FOR r IN SELECT tablename FROM pg_tables WHERE schemaname = 'public' LOOP
        EXECUTE format('DROP TABLE IF EXISTS public.%I CASCADE', r.tablename);
    END LOOP;
END $$;
"""

# 리허설용 시드. 적은 행으로 **검증하고 싶은 장치를 전부 건드린다**:
#   - regulations / meta_snapshots: 시퀀스(serial)가 있는 테이블
#   - meta_snapshots: 부분 유니크 인덱스 uq_meta_snapshot_active 의 대상
#   - pokemon: CHECK 제약 두 개(ck_pokemon_base_hp, ck_pokemon_types_differ)의 대상
SEED_SQL = """
INSERT INTO regulations (code, name, started_at, ended_at, rules) VALUES
    ('REH-A', '리허설 레귤레이션 A', '2026-01-01', NULL, '{"level": 50}'),
    ('REH-B', '리허설 레귤레이션 B', '2026-06-01', '2026-08-31', '{"level": 50}');

INSERT INTO pokemon (
    showdown_id, name_en, name_ko, display_name, form_kind,
    type1, type2, base_hp, base_atk, base_def, base_spa, base_spd, base_spe, ability_ids
) VALUES
    ('garchomp', 'Garchomp', '한카리아스', '한카리아스', 'Base',
     'dragon', 'ground', 108, 130, 95, 80, 85, 102, '["roughskin"]'),
    ('raichualola', 'Raichu-Alola', '라이츄(알로라의 모습)', '라이츄(알로라의 모습)', 'Regional',
     'electric', 'psychic', 60, 85, 50, 95, 85, 110, '["surgesurfer"]');

INSERT INTO meta_snapshots
    (regulation_id, source, format, collected_at, expires_at, is_active)
SELECT r.id, 'championsbattledata', 'Singles',
       '2026-11-01 00:00:00+00', '2026-12-01 00:00:00+00', TRUE
  FROM regulations r WHERE r.code = 'REH-A';

INSERT INTO meta_snapshots
    (regulation_id, source, format, collected_at, expires_at, is_active)
SELECT r.id, 'championsbattledata', 'Singles',
       '2026-10-01 00:00:00+00', '2026-11-01 00:00:00+00', FALSE
  FROM regulations r WHERE r.code = 'REH-A';

INSERT INTO meta_snapshots
    (regulation_id, source, format, collected_at, expires_at, is_active)
SELECT r.id, 'championsbattledata', 'Doubles',
       '2026-11-01 00:00:00+00', '2026-12-01 00:00:00+00', TRUE
  FROM regulations r WHERE r.code = 'REH-A';
"""


def pg_env(**overrides: str) -> dict[str, str]:
    """리허설 DB 를 가리키는 환경변수. libpq 표준 변수로 만든다.

    ★ 개발 DB 를 가리키면 안 된다 ★ 이 환경으로 DROP 이 돌아간다. 그래서
    `REHEARSAL_DATABASE_URL` 이 `db` 를 가리키면 즉시 실패시킨다.

    ★ MIGRATION_DATABASE_URL 도 함께 덮어쓴다 (ADR-015) ★
    alembic 은 `database_url` 이 아니라 `migration_database_url` 을 읽는다.
    여기서 둘 중 하나만 덮어쓰면 **alembic 이 개발 DB 에 마이그레이션을 돌린다.**
    조용히 일어나고, 리허설 결과는 멀쩡하게 통과한다.

    접속 계정은 **분리된 실제 계정**이다 (부트스트랩 슈퍼유저가 아니다).
    그래야 "복구 계정이 정말 복구할 수 있는가"가 함께 검증된다 — 리허설을
    슈퍼유저로 돌리면 운영에서 `champs_migrate` 로 복구가 안 되는 걸 못 잡는다.
    """
    raw = os.environ.get("REHEARSAL_DATABASE_URL") or DEFAULT_REHEARSAL_URL
    parsed = urlparse(raw.replace("+asyncpg", ""))
    host = parsed.hostname or ""
    if not host:
        pytest.fail(f"REHEARSAL_DATABASE_URL 에서 호스트를 읽을 수 없다: {raw!r}")
    if host in {"db", "localhost", "127.0.0.1"}:
        pytest.fail(
            f"리허설 DB 가 개발 DB 를 가리키고 있다 (host={host}). "
            "이 환경으로 DROP 이 실행되므로 중단한다. REHEARSAL_DATABASE_URL 을 확인하라."
        )

    database = (parsed.path or "/champs").lstrip("/")
    port = str(parsed.port or 5432)
    # DDL·DML 을 모두 하는 쪽. 리허설 본체(DROP, alembic, 시드, 복구)가 이 계정이다.
    migrate_password = os.environ.get("CHAMPS_MIGRATE_PASSWORD") or "devmigrate"
    migrate_url = f"postgresql+asyncpg://champs_migrate:{migrate_password}@{host}:{port}/{database}"

    env = os.environ.copy()
    env.update(
        {
            "PGHOST": host,
            "PGPORT": port,
            "PGUSER": "champs_migrate",
            "PGPASSWORD": migrate_password,
            "PGDATABASE": database,
            # 두 변수를 모두 리허설 DB 로 묶는다. 하나만 바꾸면 alembic 이 개발 DB 로 간다.
            "DATABASE_URL": migrate_url,
            "MIGRATION_DATABASE_URL": migrate_url,
        }
    )
    env.update(overrides)
    return env


def read_env(**overrides: str) -> dict[str, str]:
    """백업 계정(`champs_read`)으로 접속하는 환경 (ADR-015).

    백업을 읽기 전용 계정으로 돌려야 **`pg_read_all_data` 만으로 pg_dump 가
    되는지**가 함께 검증된다. 리허설을 전부 슈퍼유저로 돌리면, 운영에서
    백업 계정이 덤프를 못 만드는 상황을 못 잡는다.
    """
    password = os.environ.get("CHAMPS_READ_PASSWORD") or "devread"
    return pg_env(PGUSER="champs_read", PGPASSWORD=password, **overrides)


def bare_env(**overrides: str) -> dict[str, str]:
    """계정을 **일부러 알려주지 않는** 환경. 리허설 DB 호스트만 준다.

    ★ 왜 이런 환경이 필요한가 ★

    나머지 리허설은 PGUSER/PGPASSWORD/PGDATABASE 를 전부 명시 주입한다. 그래서
    "환경변수를 하나도 주지 않았을 때"는 아무도 보지 않았다 — 리허설 29개가 전부
    초록인데도 `docs/RUNBOOK.md` 에 적힌 짧은 명령은 깨져 있었다
    (JOURNAL 2026-11-17). 이 환경은 그 공백을 메운다.

    PGHOST 는 남긴다. 기본값이 `db`(개발 DB)라서 그대로 두면 리허설이 개발 DB 를
    향한다. 검증 대상은 **계정 기본값**이고, 호스트 기본값은 테스트가 아니라
    실제 명령으로 확인했다 (RUNBOOK 1-B / 2 의 실측 출력).
    """
    env = pg_env()
    for var in ("PGUSER", "PGPASSWORD", "PGDATABASE", "PGPORT"):
        env.pop(var, None)
    env.update(overrides)
    return env


def psql(sql: str, *, env: dict[str, str] | None = None, check: bool = True):
    """`scripts.db_state.psql` 에 리허설 기본 환경을 붙인 래퍼.

    ★ 공용 쪽은 `env` 에 기본값이 없다 — 어느 DB 를 봤는지 호출부에서 보이게
      하려는 것이다. 리허설은 거의 모든 호출이 같은 DB 라 여기서만 기본값을 준다.
    """
    return _psql(sql, env=env or pg_env(), check=check)


def scalar(sql: str, *, env: dict[str, str] | None = None) -> str:
    return _scalar(sql, env=env or pg_env())


def rows(sql: str, *, env: dict[str, str] | None = None) -> list[list[str]]:
    return _rows(sql, env=env or pg_env())


def run_script(script: Path, *args: str, env: dict[str, str] | None = None, check: bool = True):
    return subprocess.run(
        ["bash", str(script), *args],
        env=env or pg_env(),
        capture_output=True,
        text=True,
        check=check,
        timeout=300,
    )


# ★ `DatabaseState`·`capture_state` 는 `scripts/db_state.py` 로 옮겼다
#   (머리말 참조). 여기서는 리허설 기본 환경을 붙인 얇은 래퍼만 둔다.


def table_names(env: dict[str, str] | None = None) -> list[str]:
    return _table_names(env or pg_env())


def capture_state(env: dict[str, str] | None = None) -> DatabaseState:
    return capture_state_impl(env or pg_env())


@dataclass(frozen=True)
class RehearsalResult:
    """한 번의 백업→파괴→복구 사이클 결과."""

    before: DatabaseState
    after: DatabaseState
    dump_path: Path
    tables_after_destroy: int
    backup_log: str
    restore_log: str


@pytest.fixture(scope="session")
def rehearsal_env() -> dict[str, str]:
    """리허설 DB 에 실제로 붙는지 먼저 확인한다. 안 되면 건너뛰지 않고 실패한다."""
    env = pg_env()
    probe = psql("SELECT 1", env=env, check=False)
    if probe.returncode != 0:
        pytest.fail(
            "리허설 DB 에 붙을 수 없다.\n"
            f"  host={env['PGHOST']}:{env['PGPORT']} db={env['PGDATABASE']}\n"
            f"  먼저 띄워라: {START_HINT}\n"
            f"  psql stderr: {probe.stderr.strip()}"
        )
    return env


@pytest.fixture(scope="session")
def backup_env(rehearsal_env: dict[str, str]) -> dict[str, str]:
    """백업 스크립트용 환경. 읽기 전용 계정으로 붙는다 (ADR-015).

    `rehearsal_env` 에 의존해 접속 가능 여부를 먼저 확인받는다.
    """
    return read_env()


@pytest.fixture(scope="session")
def rehearsal(rehearsal_env: dict[str, str], tmp_path_factory: pytest.TempPathFactory):
    """백업 → 파괴 → 복구를 **한 번** 수행하고 전후 상태를 돌려준다.

    사이클을 테스트마다 반복하지 않는다. 같은 사이클을 여러 각도에서 단정하는
    것이므로 반복은 느려지기만 한다. 각 층의 단정은 개별 테스트로 분리해,
    깨졌을 때 **어느 층이 깨졌는지** 바로 알 수 있게 한다.
    """
    env = rehearsal_env
    backup_dir = tmp_path_factory.mktemp("backups")

    # ---- 1. 깨끗한 스키마 + 시드 ----
    psql(DESTROY_SQL, env=env)
    alembic = subprocess.run(
        ["uv", "run", "alembic", "upgrade", "head"],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )
    if alembic.returncode != 0:
        pytest.fail(f"alembic upgrade head 실패\nstdout:{alembic.stdout}\nstderr:{alembic.stderr}")
    psql(SEED_SQL, env=env)

    before = capture_state(env)

    # ---- 2. 백업 (스크립트를 실제로 실행한다) ----
    backup = run_script(
        BACKUP_SCRIPT,
        # ★ 백업만 읽기 계정이다. 복구는 DDL 이라 champs_migrate 로 돈다.
        env=read_env(BACKUP_DIR=str(backup_dir), BACKUP_RETENTION_DAYS="7"),
        check=False,
    )
    if backup.returncode != 0:
        pytest.fail(f"backup.sh 실패\nstdout:{backup.stdout}\nstderr:{backup.stderr}")
    dump_path = Path(backup.stdout.strip())

    # ---- 3. 파괴 ----
    psql(DESTROY_SQL, env=env)
    tables_after_destroy = len(table_names(env))

    # ---- 4. 복구 (스크립트를 실제로 실행한다) ----
    restore = run_script(RESTORE_SCRIPT, str(dump_path), env=env, check=False)
    if restore.returncode != 0:
        pytest.fail(f"restore.sh 실패\nstdout:{restore.stdout}\nstderr:{restore.stderr}")

    after = capture_state(env)

    return RehearsalResult(
        before=before,
        after=after,
        dump_path=dump_path,
        tables_after_destroy=tables_after_destroy,
        backup_log=backup.stderr,
        restore_log=restore.stderr,
    )
