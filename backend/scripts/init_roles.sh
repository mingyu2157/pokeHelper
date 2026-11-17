#!/usr/bin/env bash
#
# 최소 권한 DB 계정을 만들고 권한을 맞춘다 (ADR-015).
#
# `backend/sql/roles.sql` 을 **부트스트랩 계정**으로 실행한다. 역할 생성은 클러스터
# 차원의 작업이라 CREATEROLE 권한이 필요하고, 그건 분리한 세 계정 중 어느 것도
# 가지고 있지 않다 (의도한 것이다).
#
# ★ 왜 initdb 마운트만으로 끝나지 않는가 ★
# postgres 이미지의 /docker-entrypoint-initdb.d/ 는 데이터 디렉토리가 빈 **첫
# 기동에만** 실행된다. 이미 시드가 든 개발 DB 에서는 돌지 않고, `down -v` 로
# 비우면 시드가 날아간다. CI 는 서비스 컨테이너라 마운트할 자리조차 없다.
# 그래서 같은 SQL 을 손으로도 돌릴 수 있어야 한다.
#
# 멱등하다. 몇 번 돌려도 같은 상태가 된다.
#
# 사용법:
#   docker compose exec backend bash scripts/init_roles.sh
#   PGHOST=db-rehearsal docker compose exec backend bash scripts/init_roles.sh
#
# 접속 정보는 libpq 표준 환경변수로 받는다 (backup.sh 와 같은 규칙).
# 비밀번호 세 개는 CHAMPS_*_PASSWORD 로 받는다.

set -euo pipefail

SQL_FILE="${ROLES_SQL:-/app/sql/roles.sql}"

log() { echo "init_roles.sh: $*" >&2; }
die() { log "$*"; exit 1; }

[ -f "$SQL_FILE" ] || die "SQL 파일이 없다: $SQL_FILE"

# ★ 비밀번호가 비어 있으면 멈춘다.
# roles.sql 은 환경변수가 없을 때 빈 문자열로 떨어지는데, 그대로 두면 비밀번호 없는
# 계정이 조용히 만들어진다. 계정 분리의 의미가 사라지므로 여기서 막는다.
for var in CHAMPS_MIGRATE_PASSWORD CHAMPS_APP_PASSWORD CHAMPS_READ_PASSWORD; do
    value="${!var:-}"
    [ -n "$value" ] || die "$var 가 비어 있다. .env 를 확인하라 (.env.example 참조)."
done

log "대상 — host=${PGHOST:-<기본값>} db=${PGDATABASE:-<기본값>} user=${PGUSER:-<기본값>}"
log "SQL  — ${SQL_FILE}"

# 비밀번호는 `-v` 로 넘긴다. 환경변수(\getenv)가 아닌 이유는 roles.sql 머리말 참조 —
# psql 16 미만 호환성과, 변수 누락 시 조용히 빈 비밀번호가 되는 문제 때문이다.
# ON_ERROR_STOP 은 SQL 안에서도 켜지만, 메타커맨드 실패까지 잡으려면 여기도 준다.
psql \
    --no-psqlrc \
    --set=ON_ERROR_STOP=1 \
    -v migrate_password="$CHAMPS_MIGRATE_PASSWORD" \
    -v app_password="$CHAMPS_APP_PASSWORD" \
    -v read_password="$CHAMPS_READ_PASSWORD" \
    --file="$SQL_FILE"

log "완료. 세 계정의 권한을 검증하려면:"
log "  docker compose exec backend uv run pytest tests/integration/test_db_privileges.py"
