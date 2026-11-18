-- RDS 마스터 사용자에 대응하는 로컬 역할 (ADR-015, JOURNAL 2026-11-19)
--
-- ★ 왜 이 파일이 있는가 ★
--
-- `roles.sql` 은 오랫동안 **슈퍼유저**로만 실행돼 왔다. 로컬 compose 의
-- 부트스트랩 계정(`POSTGRES_USER`)이 슈퍼유저이기 때문이다. 그런데 **RDS 의
-- 마스터 사용자는 슈퍼유저가 아니다.** 그래서 "로컬에서 되는데 RDS 에서 안 되는"
-- 상태였고, 배포 당일에 알았을 것이다.
--
-- 그 차이를 없앤다. 이 파일이 **슈퍼유저가 아닌 마스터 역할**을 만들고,
-- 그 뒤 `roles.sql` 은 그 역할로 실행된다. 로컬·CI·RDS 가 같은 경로를 탄다.
-- ci.yml 이 DB 계정에 대해 이미 적어둔 원칙과 같다 — "CI 도 로컬과 같은 계정으로
-- 돌려야 권한 때문에 운영에서만 깨지는 상황을 CI 가 잡는다."
--
-- RDS 에서는 이 파일을 쓰지 않는다. 마스터 사용자를 AWS 가 만들어 주기 때문이다.
--
-- 호출자가 `-v master_password=...` 로 비밀번호를 넘긴다 (roles.sql 과 같은 규칙).

\set ON_ERROR_STOP on

SELECT current_database() AS dbname \gset

\echo '[master-role.sql] 대상 데이터베이스:' :dbname

-- ---------------------------------------------------------------------------
-- 1. 마스터 역할
-- ---------------------------------------------------------------------------
-- RDS 마스터가 가진 것: LOGIN, CREATEDB, CREATEROLE. **갖지 않은 것: SUPERUSER.**
DO $do$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'champs_master') THEN
        CREATE ROLE champs_master LOGIN CREATEDB CREATEROLE NOSUPERUSER;
    END IF;
END
$do$;

ALTER ROLE champs_master WITH LOGIN PASSWORD :'master_password'
    CREATEDB CREATEROLE NOSUPERUSER NOREPLICATION NOBYPASSRLS;

-- ---------------------------------------------------------------------------
-- 2. 이미 있는 champs_* 역할에 대한 권한
-- ---------------------------------------------------------------------------
-- 새 DB 에서는 champs_master 가 세 역할을 직접 만들므로 PG16 이 ADMIN 을 준다.
-- 하지만 **이미 슈퍼유저가 만들어 둔 DB** (기존 개발 환경)에서는 아무 권한이 없다.
-- 그러면 roles.sql 의 `ALTER ROLE` 과 `ALTER DEFAULT PRIVILEGES` 가 막힌다.
--
-- `INHERIT TRUE` 가 중요하다. PG16 의 CREATEROLE 자동 부여는 `ADMIN` 만 주고
-- `INHERIT` 를 끄는데, `ALTER DEFAULT PRIVILEGES FOR ROLE x` 는 x 에 대한
-- **USAGE**(= INHERIT 경로)를 요구한다. ADMIN 만으로는 통과하지 못한다.
DO $do$
DECLARE
    target text;
BEGIN
    FOREACH target IN ARRAY ARRAY['champs_migrate', 'champs_app', 'champs_read'] LOOP
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = target) THEN
            EXECUTE format(
                'GRANT %I TO champs_master WITH ADMIN TRUE, INHERIT TRUE', target);
        END IF;
    END LOOP;
END
$do$;

-- ---------------------------------------------------------------------------
-- 3. 데이터베이스 소유권
-- ---------------------------------------------------------------------------
-- RDS 에서는 마스터가 DB 소유자다. 로컬도 맞춘다.
--
-- 이게 없으면 roles.sql 의 `REVOKE CREATE ON SCHEMA public FROM PUBLIC` 이
-- 막힌다 — PG15+ 에서 `public` 스키마의 소유자는 `pg_database_owner` 이고,
-- DB 소유자가 아니면 그 역할의 멤버가 아니기 때문이다.
ALTER DATABASE :"dbname" OWNER TO champs_master;

\echo '[master-role.sql] champs_master 준비됨 (NOSUPERUSER)'
