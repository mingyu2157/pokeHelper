"""복구 리허설 공통 설비 (ADR-014).

★ 왜 psql 로 DB 와 대화하는가 ★

이 디렉토리는 SQLAlchemy 를 쓰지 않는다. 이유가 셋이다.

1. 리허설은 **테이블 전체를 지우고 되살린다.** ORM 은 스키마가 존재한다는 전제
   위에서 동작하므로, 파괴 직후의 상태를 다루기에 맞지 않는다.
2. 검증 대상이 행이 아니라 **제약·인덱스·시퀀스** 다. 이건 카탈로그(`pg_indexes`,
   `pg_constraint`, `pg_sequences`)를 직접 읽어야 보인다.
3. 장애 때 운영자가 실제로 쓰는 도구가 psql 이다. 같은 도구로 검증하면
   `docs/RUNBOOK.md` 의 확인 명령이 테스트로 뒷받침된다.

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


def psql(sql: str, *, env: dict[str, str] | None = None, check: bool = True):
    """psql 로 SQL 한 덩어리를 실행한다. 결과는 탭 구분 평문으로 받는다."""
    return subprocess.run(
        [
            "psql",
            "--no-psqlrc",
            "--quiet",
            "--tuples-only",
            "--no-align",
            "--field-separator=\t",
            "--set=ON_ERROR_STOP=1",
            "--command",
            sql,
        ],
        env=env or pg_env(),
        capture_output=True,
        text=True,
        check=check,
        timeout=180,
    )


def scalar(sql: str, *, env: dict[str, str] | None = None) -> str:
    return psql(sql, env=env).stdout.strip()


def rows(sql: str, *, env: dict[str, str] | None = None) -> list[list[str]]:
    out = psql(sql, env=env).stdout.strip()
    if not out:
        return []
    return [line.split("\t") for line in out.splitlines()]


def run_script(script: Path, *args: str, env: dict[str, str] | None = None, check: bool = True):
    return subprocess.run(
        ["bash", str(script), *args],
        env=env or pg_env(),
        capture_output=True,
        text=True,
        check=check,
        timeout=300,
    )


@dataclass(frozen=True)
class DatabaseState:
    """복구 전/후를 비교하기 위한 DB 스냅샷.

    행만 담지 않는다. **인덱스·제약·시퀀스까지** 담는 게 요점이다 —
    행은 맞는데 무결성 장치가 사라진 복구가 가장 위험하다.
    """

    row_counts: dict[str, int]
    checksums: dict[str, str]
    sequences: dict[str, str]
    indexes: dict[str, str]
    constraints: dict[str, str]


def table_names(env: dict[str, str] | None = None) -> list[str]:
    return [
        r[0]
        for r in rows(
            "SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename",
            env=env,
        )
    ]


def capture_state(env: dict[str, str] | None = None) -> DatabaseState:
    env = env or pg_env()
    tables = table_names(env)

    row_counts: dict[str, int] = {}
    checksums: dict[str, str] = {}
    for table in tables:
        row_counts[table] = int(scalar(f'SELECT count(*) FROM public."{table}"', env=env))
        # 행을 텍스트로 직렬화해 정렬 후 md5. 행 수가 같고 값이 깨진 복구를 잡는다.
        checksums[table] = scalar(
            f"SELECT coalesce(md5(string_agg(t::text, '|' ORDER BY t::text)), '') "
            f'FROM public."{table}" t',
            env=env,
        )

    sequences = {
        r[0]: (r[1] if len(r) > 1 else "")
        for r in rows(
            "SELECT sequencename, coalesce(last_value::text, '<unused>') "
            "FROM pg_sequences WHERE schemaname='public' ORDER BY sequencename",
            env=env,
        )
    }
    indexes = {
        r[0]: r[1]
        for r in rows(
            "SELECT indexname, indexdef FROM pg_indexes "
            "WHERE schemaname='public' ORDER BY indexname",
            env=env,
        )
    }
    constraints = {
        r[0]: r[1]
        for r in rows(
            "SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint "
            "WHERE connamespace = 'public'::regnamespace ORDER BY conname",
            env=env,
        )
    }
    return DatabaseState(row_counts, checksums, sequences, indexes, constraints)


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
