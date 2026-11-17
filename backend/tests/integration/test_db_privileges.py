"""DB 계정 권한 통합 테스트 (ADR-015).

★ `GRANT` 문이 있는 것과 권한이 집행되는 것은 다르다 ★

`sql/roles.sql` 에 GRANT 를 적어두는 것만으로는 아무것도 보증되지 않는다.
누가 나중에 `GRANT ALL` 을 한 줄 더하거나, `ALTER DEFAULT PRIVILEGES` 의
`FOR ROLE` 을 빼먹어도 **프로그램은 정상 동작한다.** 깨지는 시점은 다음
마이그레이션 이후이고, 그 시차 때문에 원인을 찾기 어렵다.

이 저장소는 같은 함정을 두 번 겪었다 — ADR-013 에서 요청 간격 상수가 선언돼
있는데 실제 경로에서는 0초였고, ADR-014 에서 제약 정의가 카탈로그에 남아 있어도
집행 여부는 별개였다. 그래서 여기서도 **위반을 실제로 실행해서 거부되는지** 본다.

psql 을 subprocess 로 쓴다. SQLAlchemy 를 쓰지 않는 이유:
- 검증 대상이 행이 아니라 **권한 오류** 다. 오류 메시지와 종료 코드가 신호다.
- 세 계정으로 각각 접속해야 한다. 엔진을 세 개 만드는 것보다 단순하다.
- 장애 때 운영자가 쓰는 도구와 같다 (docs/RUNBOOK.md).

**개발 DB 를 더럽히지 않는다.** 거부 테스트는 실패하는 연산이고, 허용 테스트는
트랜잭션을 롤백한다. 미래 객체 테스트만 실제 테이블을 만들고, 픽스처가 지운다.
"""

from __future__ import annotations

import os
import subprocess
from urllib.parse import urlparse

import pytest

from app.core.config import get_settings

pytestmark = pytest.mark.db

PROBE_TABLE = "zz_privilege_probe"

ACCOUNTS = ("champs_migrate", "champs_app", "champs_read")


def _target() -> tuple[str, str, str]:
    """개발 DB 의 host / port / dbname. 계정은 테스트가 직접 고른다."""
    parsed = urlparse(get_settings().database_url.replace("+asyncpg", ""))
    return parsed.hostname or "db", str(parsed.port or 5432), (parsed.path or "/champs").lstrip("/")


def _password(role: str) -> str:
    env_name = {
        "champs_migrate": "CHAMPS_MIGRATE_PASSWORD",
        "champs_app": "CHAMPS_APP_PASSWORD",
        "champs_read": "CHAMPS_READ_PASSWORD",
    }[role]
    default = {"champs_migrate": "devmigrate", "champs_app": "devapp", "champs_read": "devread"}[
        role
    ]
    return os.environ.get(env_name) or default


def sql_as(user: str, password: str, statement: str) -> subprocess.CompletedProcess[str]:
    """임의의 계정으로 SQL 을 실행한다. 실패를 예외로 만들지 않는다 —
    거부되는 것이 기대값인 경우가 많다.

    자격증명을 인자로 받는다. 분리된 세 계정뿐 아니라 **설정 파일에 적혀 있는
    계정이 무엇이든** 그대로 확인할 수 있어야 한다.
    """
    host, port, database = _target()
    env = os.environ.copy()
    env.update(
        {
            "PGHOST": host,
            "PGPORT": port,
            "PGDATABASE": database,
            "PGUSER": user,
            "PGPASSWORD": password,
        }
    )
    return subprocess.run(
        [
            "psql",
            "--no-psqlrc",
            "--quiet",
            "--tuples-only",
            "--no-align",
            "--set=ON_ERROR_STOP=1",
            "--command",
            statement,
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )


def sql(role: str, statement: str) -> subprocess.CompletedProcess[str]:
    """분리된 세 계정 중 하나로 실행한다."""
    return sql_as(role, _password(role), statement)


def allowed(role: str, statement: str) -> str:
    result = sql(role, statement)
    assert result.returncode == 0, f"{role} 에게 허용돼야 하는데 거부됐다:\n{result.stderr}"
    return result.stdout.strip()


def denied(role: str, statement: str, *, because: str) -> str:
    result = sql(role, statement)
    assert result.returncode != 0, f"{role} 이 {because} — 거부돼야 한다"
    return result.stderr


@pytest.fixture(scope="module", autouse=True)
def roles_exist():
    """역할이 없으면 건너뛰지 않고 실패한다.

    조용히 skip 하면 "계정을 분리했다"는 주장이 검증되지 않은 채 초록으로 남는다.
    """
    result = sql(
        "champs_app",
        "SELECT rolname FROM pg_roles WHERE rolname IN "
        "('champs_migrate','champs_app','champs_read') ORDER BY rolname",
    )
    if result.returncode != 0:
        pytest.fail(
            "분리된 계정으로 DB 에 붙을 수 없다.\n"
            "  먼저 역할을 만들어라: "
            "docker compose exec backend bash scripts/init_roles.sh\n"
            f"  psql stderr: {result.stderr.strip()}"
        )
    found = result.stdout.split()
    missing = sorted(set(ACCOUNTS) - set(found))
    assert missing == [], f"없는 역할: {missing}"


class TestRoleAttributes:
    """명세 기반 — 세 계정 모두 클러스터 차원 특권이 없어야 한다.

    이 검증이 막는 것: 디버깅 중에 `ALTER ROLE champs_app SUPERUSER` 를 하고
    되돌리지 않는 것. 그러면 아래 거부 테스트가 전부 통과하지 못하지만,
    원인을 여기서 바로 알 수 있다.
    """

    def test_no_account_has_cluster_privileges(self):
        rows = allowed(
            "champs_read",
            "SELECT rolname, rolsuper, rolcreatedb, rolcreaterole, rolreplication, rolbypassrls "
            "FROM pg_roles WHERE rolname IN "
            "('champs_migrate','champs_app','champs_read') ORDER BY rolname",
        )
        for line in rows.splitlines():
            name, *flags = line.split("|")
            assert flags == ["f"] * 5, f"{name} 에 특권이 켜져 있다: {flags}"


class TestAppAccount:
    """결정 테이블 — 앱 계정은 DML 4종만 허용, DDL 전부 거부.

    이 검증이 막는 것: 앱 계정이 스키마를 바꿀 수 있는 상태. 애플리케이션
    버그나 SQL 인젝션이 테이블을 날리는 경로가 열린다.
    """

    def test_select_is_allowed(self):
        assert allowed("champs_app", "SELECT count(*) FROM pokemon").isdigit()

    def test_write_cycle_is_allowed(self):
        """INSERT·UPDATE·DELETE 를 한 트랜잭션에서 돌리고 롤백한다."""
        allowed(
            "champs_app",
            """
            BEGIN;
            INSERT INTO regulations (code, name, started_at, rules)
            VALUES ('ZZ-PRIV', '권한 확인', '2026-11-17', '{}');
            UPDATE regulations SET name = '권한 확인 2' WHERE code = 'ZZ-PRIV';
            DELETE FROM regulations WHERE code = 'ZZ-PRIV';
            ROLLBACK;
            """,
        )

    def test_create_table_is_denied(self):
        stderr = denied(
            "champs_app",
            "CREATE TABLE zz_app_should_not_create (i int)",
            because="테이블을 만들 수 있다",
        )
        assert "permission denied" in stderr

    def test_drop_table_is_denied(self):
        denied("champs_app", "DROP TABLE pokemon", because="테이블을 지울 수 있다")

    def test_alter_table_is_denied(self):
        denied(
            "champs_app",
            "ALTER TABLE pokemon ADD COLUMN zz_should_not_exist int",
            because="테이블 구조를 바꿀 수 있다",
        )

    def test_truncate_is_denied(self):
        """TRUNCATE 는 DELETE 와 별개 권한이다. 주지 않았다 —
        시드 적재가 upsert 와 DELETE 만 쓰는 것을 확인했다."""
        denied("champs_app", "TRUNCATE pokemon", because="테이블을 비울 수 있다")

    def test_cannot_create_role(self):
        denied("champs_app", "CREATE ROLE zz_evil LOGIN", because="역할을 만들 수 있다")


class TestReadOnlyAccount:
    """결정 테이블 — 읽기 계정은 SELECT 만.

    이 검증이 막는 것: 분석·디버깅용 계정으로 데이터가 바뀌는 것.
    """

    def test_select_is_allowed(self):
        assert allowed("champs_read", "SELECT count(*) FROM pokemon").isdigit()

    def test_insert_is_denied(self):
        stderr = denied(
            "champs_read",
            "INSERT INTO regulations (code, name, started_at, rules) "
            "VALUES ('ZZ-RO', 'x', '2026-11-17', '{}')",
            because="데이터를 넣을 수 있다",
        )
        assert "permission denied" in stderr

    def test_delete_is_denied(self):
        denied("champs_read", "DELETE FROM regulations", because="데이터를 지울 수 있다")

    def test_create_table_is_denied(self):
        denied("champs_read", "CREATE TABLE zz_ro (i int)", because="테이블을 만들 수 있다")


class TestFutureObjects:
    """★ 이 클래스가 이 파일의 핵심이다 — 다음 마이그레이션에서 터지는 버그를 막는다.

    `GRANT ... ON ALL TABLES` 는 **실행 시점의** 테이블에만 적용된다. 새 테이블이
    추가되면 앱이 그것을 못 읽는다. 오늘은 전부 통과하고 **다음 마이그레이션 뒤에
    앱이 죽는다.**

    정의(`pg_default_acl`)와 집행(실제로 테이블을 만들고 접근) 둘 다 본다.
    ADR-014 에서 배운 것 — 카탈로그에 정의가 있어도 집행은 별개 단정이다.
    """

    @pytest.fixture
    def probe_table(self):
        """`champs_migrate` 가 새 테이블을 만든다. 끝나면 지운다.

        alembic 이 하는 일(= migrate 계정이 테이블을 만드는 것)을 그대로 재현한다.
        """
        allowed("champs_migrate", f"DROP TABLE IF EXISTS {PROBE_TABLE}")
        allowed("champs_migrate", f"CREATE TABLE {PROBE_TABLE} (id serial PRIMARY KEY, note text)")
        try:
            yield PROBE_TABLE
        finally:
            allowed("champs_migrate", f"DROP TABLE IF EXISTS {PROBE_TABLE}")

    def test_default_privileges_are_registered_for_the_migrate_role(self):
        """정의 층 — `FOR ROLE champs_migrate` 를 빼먹으면 여기서 깨진다.

        생략하면 기본 권한이 **스크립트를 실행한 역할**(부트스트랩)에 묶이고,
        정작 alembic 이 만드는 객체에는 적용되지 않는다.
        """
        rows = allowed(
            "champs_read",
            "SELECT pg_get_userbyid(defaclrole), defaclobjtype, defaclacl::text "
            "FROM pg_default_acl WHERE defaclnamespace = 'public'::regnamespace",
        )
        assert rows, "public 스키마에 등록된 기본 권한이 없다"
        assert "champs_migrate" in rows, f"champs_migrate 용 기본 권한이 없다:\n{rows}"
        assert "champs_app" in rows, f"champs_app 에게 주는 기본 권한이 없다:\n{rows}"

    def test_app_can_write_a_newly_created_table(self, probe_table: str):
        """집행 층 — 정의가 맞아도 실제로 되는지는 별개다."""
        allowed("champs_app", f"INSERT INTO {probe_table} (note) VALUES ('ok')")
        assert allowed("champs_app", f"SELECT count(*) FROM {probe_table}") == "1"

    def test_app_can_use_the_new_sequence(self, probe_table: str):
        """시퀀스 권한은 테이블 권한과 별개다. 빠지면 serial INSERT 가 실패한다."""
        allowed("champs_app", f"INSERT INTO {probe_table} (note) VALUES ('seq')")
        assert allowed("champs_app", f"SELECT max(id) FROM {probe_table}") == "1"

    def test_readonly_can_read_a_newly_created_table(self, probe_table: str):
        """`pg_read_all_data` 는 미래 테이블까지 포함한다.
        일회성 `GRANT SELECT ON ALL TABLES` 로 바꾸면 여기서 깨진다."""
        assert allowed("champs_read", f"SELECT count(*) FROM {probe_table}") == "0"

    def test_app_still_cannot_alter_a_newly_created_table(self, probe_table: str):
        """새 테이블에도 DDL 은 막혀 있어야 한다. 기본 권한이 과하게 열렸는지 본다."""
        denied(
            "champs_app",
            f"ALTER TABLE {probe_table} ADD COLUMN zz int",
            because="새 테이블의 구조를 바꿀 수 있다",
        )


class TestRuntimeConfiguration:
    """★ 설정이 분리를 실제로 쓰는지 — 계정을 만들어두고 안 쓰면 의미가 없다.

    이 검증이 막는 것: `.env` 의 `DATABASE_URL` 이 부트스트랩 슈퍼유저로
    되돌아가는 것. 계정은 그대로 존재하므로 위의 모든 테스트가 통과하는데,
    **애플리케이션은 슈퍼유저로 돈다.**
    """

    @staticmethod
    def _user(url: str) -> str:
        return urlparse(url.replace("+asyncpg", "")).username or ""

    def test_app_url_does_not_use_the_bootstrap_account(self):
        user = self._user(get_settings().database_url)
        assert user == "champs_app", (
            f"앱이 {user!r} 로 붙고 있다. .env 의 DATABASE_URL 을 champs_app 으로 바꿔라 "
            "(.env.example 참조)."
        )

    def test_migration_url_uses_the_ddl_account(self):
        assert self._user(get_settings().migration_database_url) == "champs_migrate"

    def test_app_and_migration_accounts_differ(self):
        settings = get_settings()
        assert self._user(settings.database_url) != self._user(settings.migration_database_url)

    def test_the_configured_app_account_cannot_run_ddl(self):
        """URL 에 적힌 계정이 정말 DDL 을 못 하는지 **그 계정으로** 확인한다.

        이름이 무엇이든 자격증명을 URL 에서 읽는다. 이름만 `champs_app` 이고
        권한이 몰려 있는 경우, 그리고 부트스트랩 계정이 설정된 경우를 모두 잡는다.
        """
        parsed = urlparse(get_settings().database_url.replace("+asyncpg", ""))
        user = parsed.username or ""
        password = parsed.password or ""
        table = "zz_configured_app_should_not_create"
        try:
            result = sql_as(user, password, f"CREATE TABLE {table} (i int)")
        finally:
            # ★ 이 단정이 실패했다는 것은 CREATE 가 **성공했다**는 뜻이다.
            #   정리하지 않으면 실패할 때마다 개발 DB 에 테이블이 쌓인다.
            #   만든 계정으로 지운다 — 다른 계정은 소유자가 아니라 지울 수 없다.
            sql_as(user, password, f"DROP TABLE IF EXISTS {table}")

        assert result.returncode != 0, (
            f"설정된 앱 계정 {user!r} 이 테이블을 만들 수 있다. "
            "DATABASE_URL 이 DDL 권한을 가진 계정을 가리킨다 (.env.example 참조)."
        )
