-- 최소 권한 DB 계정 (ADR-015)
--
-- ★ 멱등하다 ★ 여러 번 실행해도 같은 상태가 된다. 그래야 두 경로에서 같은 파일을
-- 쓸 수 있다.
--
--   1. 새 컨테이너 첫 기동 — compose 가 /docker-entrypoint-initdb.d/ 로 마운트한다
--   2. 이미 데이터가 있는 DB — backend/scripts/init_roles.sh 로 수동 1회
--
-- 2번이 왜 필요한가: postgres 이미지의 initdb 스크립트는 **데이터 디렉토리가 빈
-- 첫 기동에만** 실행된다. `pgdata` 볼륨에 이미 시드가 들어 있으면 돌지 않고,
-- `down -v` 로 비우면 시드가 날아간다. CI 는 서비스 컨테이너라 마운트할 자리도 없다.
--
-- 비밀번호는 호출자가 `psql -v` 로 넘긴다. SQL 파일에 박으면 저장소에 비밀이 들어간다.
--
--   psql -v migrate_password=... -v app_password=... -v read_password=... -f roles.sql
--
-- ★ 왜 `\getenv` 가 아니라 `-v` 인가 ★
-- `\getenv` 는 psql 16 부터다. CI 러너의 psql 버전을 보장할 수 없어서 쓰지 않는다.
-- 그리고 `-v` 가 **더 안전하다** — 변수를 안 넘기면 `:'app_password'` 가 그대로 남아
-- 문법 오류로 즉시 실패한다. `\getenv` 는 환경변수가 없으면 빈 문자열이 되어
-- **비밀번호 없는 계정이 조용히 만들어진다.** 실측으로 확인한 차이다.

\set ON_ERROR_STOP on

SELECT current_database() AS dbname \gset

\echo '[roles.sql] 대상 데이터베이스:' :dbname

-- ---------------------------------------------------------------------------
-- 1. 역할 생성 (없을 때만)
-- ---------------------------------------------------------------------------
DO $do$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'champs_migrate') THEN
        CREATE ROLE champs_migrate LOGIN;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'champs_app') THEN
        CREATE ROLE champs_app LOGIN;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'champs_read') THEN
        CREATE ROLE champs_read LOGIN;
    END IF;
END
$do$;

-- 비밀번호는 매번 다시 설정한다. 환경변수를 바꾸면 그게 반영돼야 한다.
ALTER ROLE champs_migrate WITH LOGIN PASSWORD :'migrate_password';
ALTER ROLE champs_app     WITH LOGIN PASSWORD :'app_password';
ALTER ROLE champs_read    WITH LOGIN PASSWORD :'read_password';

-- 세 역할 모두 클러스터 차원의 특권을 갖지 않는다. 기본값이지만 명시한다.
--
-- ★ 슈퍼유저냐 아니냐로 동작이 갈린다 ★
--
-- `ALTER ROLE ... NOSUPERUSER` 는 **슈퍼유저만** 실행할 수 있다
-- (`NOREPLICATION`·`NOBYPASSRLS` 도 같다). 그런데 RDS 의 마스터 사용자는
-- 슈퍼유저가 아니다. 예전 버전은 이 세 줄 때문에 RDS 에서 통째로 실패했다
-- (JOURNAL 2026-11-19).
--
-- 그래서 **강제할 수 있으면 강제하고, 없으면 검증한다.** 검증에서 걸리면
-- 조용히 넘어가지 않고 실패한다 — "특권이 붙은 줄 모르고 서비스하는 것"이
-- 이 블록이 막으려던 바로 그 상태이기 때문이다.
DO $do$
DECLARE
    target   text;
    offender text[] := '{}';
    is_super boolean;
BEGIN
    SELECT rolsuper INTO is_super FROM pg_roles WHERE rolname = CURRENT_USER;

    FOREACH target IN ARRAY ARRAY['champs_migrate', 'champs_app', 'champs_read'] LOOP
        IF is_super THEN
            EXECUTE format(
                'ALTER ROLE %I WITH NOSUPERUSER NOCREATEDB NOCREATEROLE '
                'NOREPLICATION NOBYPASSRLS', target);
        ELSIF EXISTS (
            SELECT 1 FROM pg_roles
             WHERE rolname = target
               AND (rolsuper OR rolcreatedb OR rolcreaterole
                    OR rolreplication OR rolbypassrls)
        ) THEN
            offender := offender || target;
        END IF;
    END LOOP;

    IF array_length(offender, 1) IS NOT NULL THEN
        RAISE EXCEPTION
            '[roles.sql] 클러스터 특권이 붙은 역할: %. 슈퍼유저로 접속해 되돌려야 한다 '
            '(현재 사용자 % 는 슈퍼유저가 아니라 고칠 수 없다)', offender, CURRENT_USER;
    END IF;
END
$do$;

-- ★ 이 스크립트를 돌리는 역할이 champs_migrate 의 **멤버**여야 한다 ★
--
-- 아래 5장의 `ALTER DEFAULT PRIVILEGES FOR ROLE champs_migrate` 가 그 역할에
-- 대한 USAGE 멤버십을 요구한다. 슈퍼유저는 모든 역할을 암묵적으로 가지므로
-- 문제가 없지만, RDS 마스터처럼 CREATEROLE 만 있는 역할은 다르다 —
-- PG16 은 CREATEROLE 역할이 만든 역할에 **ADMIN 은 주되 INHERIT 는 끈 채로**
-- 멤버십을 준다. 그래서 "내가 만든 역할인데 기본 권한을 못 바꾸는" 상태가 된다.
DO $do$
BEGIN
    IF NOT pg_has_role(CURRENT_USER, 'champs_migrate', 'USAGE') THEN
        EXECUTE 'GRANT champs_migrate TO CURRENT_USER WITH INHERIT TRUE';
        RAISE NOTICE '[roles.sql] % 에게 champs_migrate 멤버십(INHERIT) 부여', CURRENT_USER;
    END IF;
END
$do$;

-- ---------------------------------------------------------------------------
-- 2. 데이터베이스·스키마 접근
-- ---------------------------------------------------------------------------
GRANT CONNECT ON DATABASE :"dbname" TO champs_migrate, champs_app, champs_read;

-- PG15 부터 public 스키마는 PUBLIC 에게 CREATE 를 주지 않는다. 명시적으로 준다.
GRANT USAGE          ON SCHEMA public TO champs_app, champs_read;
GRANT USAGE, CREATE  ON SCHEMA public TO champs_migrate;

-- PUBLIC(모든 역할)의 생성 권한을 거둔다. PG15+ 의 기본값과 같지만, 하위 버전에서
-- 복구한 DB 나 수동으로 되살린 경우를 막는다.
-- USAGE 는 건드리지 않는다 — 거두면 이 DB 에 붙는 다른 역할이 조용히 깨진다.
REVOKE CREATE ON SCHEMA public FROM PUBLIC;

-- ---------------------------------------------------------------------------
-- 3. 기존 객체의 소유권을 champs_migrate 로 옮긴다
-- ---------------------------------------------------------------------------
-- 이게 없으면: 기존 테이블은 부트스트랩 계정(POSTGRES_USER) 소유로 남고,
-- alembic 을 champs_migrate 로 돌리는 순간 `ALTER TABLE` 이 권한 오류로 실패한다.
-- 새 DB 에서는 대상이 0건이라 아무 일도 하지 않는다.
DO $do$
DECLARE
    obj record;
    moved int := 0;
BEGIN
    FOR obj IN
        SELECT tablename AS name FROM pg_tables
         WHERE schemaname = 'public' AND tableowner <> 'champs_migrate'
    LOOP
        EXECUTE format('ALTER TABLE public.%I OWNER TO champs_migrate', obj.name);
        moved := moved + 1;
    END LOOP;

    FOR obj IN
        SELECT sequencename AS name FROM pg_sequences
         WHERE schemaname = 'public' AND sequenceowner <> 'champs_migrate'
    LOOP
        EXECUTE format('ALTER SEQUENCE public.%I OWNER TO champs_migrate', obj.name);
        moved := moved + 1;
    END LOOP;

    RAISE NOTICE '[roles.sql] 소유권 이전: % 개 객체', moved;
END
$do$;

-- ---------------------------------------------------------------------------
-- 4. 현재 객체에 대한 권한
-- ---------------------------------------------------------------------------
-- 앱: DML 네 가지만. CREATE/DROP/ALTER 는 주지 않는다.
-- TRUNCATE 도 주지 않는다 — 시드 적재가 upsert 와 DELETE 만 쓰는 것을 확인했다.
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES    IN SCHEMA public TO champs_app;
GRANT USAGE, SELECT                  ON ALL SEQUENCES IN SCHEMA public TO champs_app;

-- 읽기 전용: pg_read_all_data (PG14+) 는 **미래 테이블까지** 자동으로 포함한다.
-- `GRANT SELECT ON ALL TABLES` 는 실행 시점의 테이블만 잡아서, 다음 마이그레이션
-- 뒤에 조용히 구멍이 생긴다.
--
-- ★ 다만 미리 정의된 역할을 부여하려면 그 역할에 대한 ADMIN 옵션이 필요하다.
--   슈퍼유저와 `rds_superuser` 멤버는 갖고 있지만, 평범한 CREATEROLE 역할은
--   갖고 있지 않다. 못 받는 환경에서는 **기본 권한으로 대체**한다 —
--   champs_migrate 가 만드는 테이블에 한해 같은 보장을 준다.
--   (대체 경로는 **다른 역할이 만든 테이블을 놓친다.** 우리는 alembic 이
--    champs_migrate 로만 DDL 을 돌리므로 성립하지만, 전제가 깨지면 구멍이 된다.)
DO $do$
BEGIN
    EXECUTE 'GRANT pg_read_all_data TO champs_read';
    RAISE NOTICE '[roles.sql] champs_read: pg_read_all_data (미래 테이블 자동 포함)';
EXCEPTION WHEN insufficient_privilege THEN
    RAISE WARNING
        '[roles.sql] pg_read_all_data 를 줄 수 없다 (% 에게 ADMIN 옵션이 없다). '
        '기본 권한으로 대체한다 — champs_migrate 가 만든 테이블만 포함된다.', CURRENT_USER;
    EXECUTE 'GRANT SELECT ON ALL TABLES    IN SCHEMA public TO champs_read';
    EXECUTE 'GRANT SELECT ON ALL SEQUENCES IN SCHEMA public TO champs_read';
    EXECUTE 'ALTER DEFAULT PRIVILEGES FOR ROLE champs_migrate IN SCHEMA public '
            'GRANT SELECT ON TABLES TO champs_read';
    EXECUTE 'ALTER DEFAULT PRIVILEGES FOR ROLE champs_migrate IN SCHEMA public '
            'GRANT SELECT ON SEQUENCES TO champs_read';
END
$do$;

-- ---------------------------------------------------------------------------
-- 5. ★ 미래에 만들어질 객체에 대한 권한
-- ---------------------------------------------------------------------------
-- 이 블록이 이 파일에서 가장 틀리기 쉬운 부분이다.
--
-- `GRANT ... ON ALL TABLES` 는 **지금 있는** 테이블에만 적용된다. 다음 마이그레이션이
-- 테이블을 하나 추가하면 champs_app 은 그 테이블을 못 읽는다. 오늘은 테스트가
-- 통과하고 **다음 마이그레이션에서 앱이 죽는다.** 시차 때문에 원인을 찾기 어렵다.
--
-- `FOR ROLE champs_migrate` 가 핵심이다. 기본 권한은 "누가 만든 객체인지"에 묶이므로,
-- 생성자(alembic 이 쓰는 역할)를 지정해야 한다. 생략하면 이 스크립트를 실행한
-- 역할(부트스트랩)이 만든 객체에만 적용되고, 정작 alembic 이 만드는 것은 빠진다.
ALTER DEFAULT PRIVILEGES FOR ROLE champs_migrate IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO champs_app;
ALTER DEFAULT PRIVILEGES FOR ROLE champs_migrate IN SCHEMA public
    GRANT USAGE, SELECT ON SEQUENCES TO champs_app;

-- ---------------------------------------------------------------------------
-- 6. 결과 요약 (사람이 읽는다)
-- ---------------------------------------------------------------------------
\echo '[roles.sql] 역할'
SELECT rolname AS role, rolsuper AS superuser, rolcreatedb AS createdb, rolcreaterole AS createrole
  FROM pg_roles
 WHERE rolname IN ('champs_migrate', 'champs_app', 'champs_read')
 ORDER BY rolname;

\echo '[roles.sql] public 스키마 테이블 소유자'
SELECT tableowner AS owner, count(*) AS tables
  FROM pg_tables WHERE schemaname = 'public'
 GROUP BY 1 ORDER BY 1;
