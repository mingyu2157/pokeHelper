#!/bin/sh
#
# postgres 이미지의 initdb 훅 (ADR-015).
#
# compose 가 이 파일을 /docker-entrypoint-initdb.d/ 에 마운트한다. 엔트리포인트는
# **데이터 디렉토리가 빈 첫 기동에만** 이 디렉토리를 실행하므로, 새 환경에서
# `docker compose up` 만으로 계정이 준비된다.
#
# ★ 왜 roles.sql 을 직접 마운트하지 않는가 ★
# 엔트리포인트는 /docker-entrypoint-initdb.d/*.sql 을 `psql -f` 로 그냥 실행한다.
# 그러면 `-v` 로 비밀번호를 넘길 방법이 없다. 그래서 .sh 훅이 psql 호출을 감싼다.
# roles.sql 은 initdb 디렉토리 밖(/opt/roles.sql)에 마운트된다 — 같은 디렉토리에
# 두면 엔트리포인트가 변수 없이 한 번 더 실행해버린다.
#
# 이미 데이터가 있는 DB 와 CI 는 backend/scripts/init_roles.sh 가 같은 SQL 을 돌린다.

set -eu

SQL_FILE=/opt/roles.sql

if [ ! -f "$SQL_FILE" ]; then
    echo "initdb-roles.sh: $SQL_FILE 가 없다. compose 의 마운트를 확인하라." >&2
    exit 1
fi

# 비밀번호가 비면 비밀번호 없는 계정이 만들어진다. 계정 분리의 의미가 사라지므로 막는다.
for name in CHAMPS_MIGRATE_PASSWORD CHAMPS_APP_PASSWORD CHAMPS_READ_PASSWORD; do
    eval "value=\${$name:-}"
    if [ -z "$value" ]; then
        echo "initdb-roles.sh: $name 가 비어 있다. compose 의 environment 를 확인하라." >&2
        exit 1
    fi
done

echo "initdb-roles.sh: 최소 권한 계정을 만든다 (db=$POSTGRES_DB)"

psql --no-psqlrc \
     --username "$POSTGRES_USER" \
     --dbname "$POSTGRES_DB" \
     --set=ON_ERROR_STOP=1 \
     -v migrate_password="$CHAMPS_MIGRATE_PASSWORD" \
     -v app_password="$CHAMPS_APP_PASSWORD" \
     -v read_password="$CHAMPS_READ_PASSWORD" \
     -f "$SQL_FILE"
