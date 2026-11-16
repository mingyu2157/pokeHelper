#!/usr/bin/env bash
#
# DB 복구 (ADR-014).
#
# ★ 이 스크립트가 막는 것: 반쯤 복구된 DB ★
#
# 복구가 중간에 실패하면 "데이터는 일부 있고 제약은 없는" 상태가 남는다. 이건
# 복구 실패보다 나쁘다 — 서비스가 돌아가면서 틀린 데이터를 받아들인다.
# 그래서 `--single-transaction` 으로 전부-아니면-전무로 만든다. PostgreSQL 은
# DDL 도 트랜잭션이라 DROP/CREATE 까지 한 트랜잭션에 묶인다.
#
# `--clean --if-exists` 로 기존 객체를 먼저 지운다. 지우지 않고 복구하면 기존
# 행 위에 덧씌워져 "복구했는데 옛 데이터가 섞여 있는" 상태가 된다.
#
# ★ 복구되지 않는 것 ★
# ROLE(계정)과 클러스터 전역 권한은 pg_dump 에 들어 있지 않다. 빈 서버에
# 복구할 때는 계정을 먼저 만들어야 한다 (docs/RUNBOOK.md 참조).
#
# 사용법:
#   docker compose exec backend bash scripts/restore.sh /app/backups/champs-20261117T...Z.dump
#
# 접속 정보는 libpq 표준 환경변수(PGHOST, PGUSER, PGPASSWORD, PGDATABASE)로 받는다.

set -euo pipefail

DB_NAME="${PGDATABASE:-champs}"

log() { echo "restore.sh: $*" >&2; }
die() { log "$*"; exit 1; }

dump="${1:-}"
[ -n "$dump" ] || die "사용법: restore.sh <덤프파일>"
[ -f "$dump" ] || die "덤프 파일이 없다: $dump"
[ -s "$dump" ] || die "덤프가 비어 있다: $dump"

# 복구를 시작하기 전에 읽을 수 있는 아카이브인지 본다. 손상된 덤프로 --clean 을
# 걸면 기존 객체를 지운 뒤에 실패할 수 있다. 지우기 전에 확인한다.
pg_restore --list "$dump" > /dev/null 2>&1 \
    || die "덤프 목차를 읽을 수 없다. 손상된 아카이브다: $dump"

log "복구 시작 — db=${DB_NAME} host=${PGHOST:-<기본값>} file=${dump}"

# --no-owner: 덤프를 만든 역할이 대상 서버에 없을 수 있다. 소유권 설정 실패로
#   전체 트랜잭션이 깨지는 것을 막는다. 접속한 역할이 소유자가 된다.
# --single-transaction 은 --exit-on-error 를 포함한다.
pg_restore \
    --dbname="$DB_NAME" \
    --clean --if-exists \
    --no-owner \
    --single-transaction \
    "$dump"

log "복구 완료 — ${dump}"
