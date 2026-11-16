#!/usr/bin/env bash
#
# DB 논리 백업 (ADR-014).
#
# ★ 이 스크립트가 막는 것: 실패한 백업이 성공처럼 보이는 것 ★
#
# 백업의 최악 실패 모드는 "백업이 없는 것"이 아니라 **"쓸 수 없는 백업이 있는 것"**
# 이다. 0 바이트 파일이나 중간에 끊긴 덤프가 보존 디렉토리에 쌓이면 사람은
# "백업 있음"으로 읽고, 정작 복구가 필요한 날에 알게 된다.
#
# 그래서 세 관문을 통과해야만 최종 파일명을 얻는다.
#   1. pg_dump 가 0 으로 끝난다
#   2. 결과 파일이 비어 있지 않다
#   3. pg_restore --list 로 아카이브 목차를 읽을 수 있다  ← 끊긴 덤프를 여기서 잡는다
# 통과 전에는 `.partial` 로 남아 있고, 실패하면 지워진다.
#
# ★ 이 백업에 들어 있지 않은 것 (중요) ★
# pg_dump 는 **데이터베이스 하나**를 덤프한다. ROLE(계정)·권한 부여는 클러스터
# 전역 객체라서 들어가지 않는다. 즉 이 덤프만으로는 **빈 서버에 완전 복구가 안 된다** —
# 계정이 먼저 있어야 한다. 계정 분리 작업(세션 B)에서 `pg_dumpall --roles-only` 를
# 함께 다룬다. 지금은 알려진 공백이고, docs/RUNBOOK.md 에 적어뒀다.
#
# 접속 정보는 libpq 표준 환경변수로 받는다 (PGHOST, PGPORT, PGUSER, PGPASSWORD,
# PGDATABASE). DATABASE_URL 을 파싱하지 않는다 — 파싱기를 하나 더 만들면
# 거기서 틀린다.
#
# 사용법:
#   docker compose exec backend bash scripts/backup.sh
#   BACKUP_DIR=/tmp/b BACKUP_RETENTION_DAYS=3 bash scripts/backup.sh
#
# 성공하면 생성된 덤프 경로를 표준출력으로 한 줄 출력한다. 로그는 표준오류로 간다.

set -euo pipefail

BACKUP_DIR="${BACKUP_DIR:-/app/backups}"
RETENTION_DAYS="${BACKUP_RETENTION_DAYS:-7}"
DB_NAME="${PGDATABASE:-champs}"

log() { echo "backup.sh: $*" >&2; }
die() { log "$*"; exit 1; }

case "$RETENTION_DAYS" in
    ''|*[!0-9]*) die "BACKUP_RETENTION_DAYS 는 0 이상의 정수여야 한다: $RETENTION_DAYS" ;;
esac

mkdir -p "$BACKUP_DIR"

# UTC 로 찍는다. 로컬 시간대로 찍으면 서버 시간대가 바뀔 때 정렬이 깨진다.
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
final="${BACKUP_DIR}/${DB_NAME}-${stamp}.dump"
partial="${final}.partial"

# 실패하거나 중간에 죽으면 반쪽 파일을 남기지 않는다.
cleanup() { rm -f "$partial"; }
trap cleanup EXIT

# --format=custom: pg_restore 가 읽는 형식. 스키마·인덱스·제약·시퀀스를 함께 담고
#   선택적 복구가 가능하다. 평문 SQL(-Fp)은 목차를 읽을 수 없어 관문 3 을 못 만든다.
log "덤프 시작 — db=${DB_NAME} host=${PGHOST:-<기본값>}"
if ! pg_dump --format=custom --file="$partial"; then
    die "pg_dump 가 실패했다. 덤프를 남기지 않는다."
fi

# 관문 2 — 비어 있지 않은가
[ -s "$partial" ] || die "덤프가 비어 있다. 실패로 처리한다."

# 관문 3 — 아카이브 목차를 읽을 수 있는가 (끊긴 덤프 탐지)
if ! pg_restore --list "$partial" > /dev/null 2>&1; then
    die "덤프 목차를 읽을 수 없다. 손상된 아카이브다."
fi

mv "$partial" "$final"
trap - EXIT

size="$(wc -c < "$final" | tr -d ' ')"
log "덤프 완료 — ${final} (${size} bytes)"

# ---- 보존 기간 정리 ----
#
# 라이선스상 영구 미러가 금지돼 있고(CLAUDE.md 8-2), 백업도 무한 누적하지 않는다.
#
# -mtime 이 아니라 -mmin 을 쓴다. -mtime 은 하루 단위로 내림하므로 경계가 최대
# 24시간 흐려지고, 테스트로 경계를 집을 수 없다. 분 단위면 경계가 선명하다.
#   -mmin +N  →  N 분보다 **더** 오래된 것. 정확히 N 분이면 남는다 (유효 쪽).
# ★ 방금 만든 백업은 어떤 설정에서도 지우지 않는다 ★
# GNU find 의 -mmin 은 경과 시간을 **올림**한다. 3초 전 파일도 "1분"이 되므로
# RETENTION_DAYS=0 이면 방금 만든 덤프가 정리 대상에 들어간다. 실제로 그렇게
# 동작했고, 복구 리허설 테스트가 잡았다 (JOURNAL 2026-11-17).
# 설정값이 무엇이든 **이번 실행의 산출물을 지우는 백업은 백업이 아니다.**
keep="$(basename "$final")"

minutes=$(( RETENTION_DAYS * 24 * 60 ))
deleted=0
while IFS= read -r -d '' old; do
    rm -f "$old"
    log "보존 기간 초과 삭제 — $(basename "$old")"
    deleted=$(( deleted + 1 ))
done < <(find "$BACKUP_DIR" -maxdepth 1 -type f \
             \( -name '*.dump' -o -name '*.dump.partial' \) \
             ! -name "$keep" \
             -mmin "+${minutes}" -print0)

log "보존 기간 ${RETENTION_DAYS}일 — ${deleted}건 삭제"

echo "$final"
