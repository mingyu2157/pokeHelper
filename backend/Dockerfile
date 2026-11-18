# syntax=docker/dockerfile:1

FROM python:3.12-slim AS base
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app
WORKDIR /app
RUN pip install --no-cache-dir uv

# ---- deps: prod 이미지에 넣을 런타임 의존성만 ----
FROM base AS deps
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

# ---- dev: 소스는 compose 가 마운트한다. /app/.venv 는 익명 볼륨으로 보호 ----
FROM base AS dev
# pg_dump / psql (ADR-014).
#
# ★ 왜 dev 이미지에 DB 클라이언트가 필요한가 ★
# 복구 리허설 테스트가 `scripts/backup.sh` 를 subprocess 로 실제 실행한다.
# 테스트가 pg_dump 를 직접 부르면 PostgreSQL 이 동작하는 것만 확인되고
# 우리 백업 절차에 대해서는 아무것도 증명하지 않는다.
# 덤으로 psql 이 생겨서 RUNBOOK 의 확인 명령을 이 컨테이너에서 전부 돌릴 수 있다.
#
# ★ 버전은 서버와 메이저를 맞춘다 (리허설 테스트가 잡은 결함) ★
#
# 처음엔 Debian 기본 패키지(trixie 는 17)를 쓰려 했다. "상위 클라이언트는 하위
# 서버에 괜찮다"고 알고 있었기 때문이다. **덤프에는 맞고 복구에는 틀렸다.**
#
#   pg_dump 17 → 서버 16 : 성공. 덤프 파일이 정상 생성된다.
#   pg_restore 17 → 서버 16 : 실패.
#       ERROR: unrecognized configuration parameter "transaction_timeout"
#       Command was: SET transaction_timeout = 0;
#
# `transaction_timeout` 은 PG17 에 추가된 설정이고, pg_restore 가 복구 서두에
# 무조건 넣는다. 즉 **백업은 되는데 복구가 안 되는** 조합이었다. 복구 리허설
# 테스트가 첫 실행에서 이걸 잡았다 (docs/decisions/JOURNAL.md 2026-11-17).
#
# 그래서 PGDG 저장소에서 서버와 같은 메이저(16)를 받는다. 코드네임은 하드코딩하지
# 않는다 — 베이스 이미지가 bookworm→trixie 로 바뀐 적이 있고, 그때 조용히 깨졌다.
#
# prod 에는 넣지 않는다 — 운영 백업은 관리형(RDS 스냅샷)이고, 운영 이미지에
# DB 클라이언트를 두면 공격 표면만 늘어난다.
ENV PGDG_KEYRING=/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc
RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends curl ca-certificates; \
    install -d /usr/share/postgresql-common/pgdg; \
    curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc -o "$PGDG_KEYRING"; \
    . /etc/os-release; \
    echo "deb [signed-by=$PGDG_KEYRING] https://apt.postgresql.org/pub/repos/apt ${VERSION_CODENAME}-pgdg main" \
        > /etc/apt/sources.list.d/pgdg.list; \
    apt-get update; \
    apt-get install -y --no-install-recommends postgresql-client-16; \
    rm -rf /var/lib/apt/lists/*; \
    pg_dump --version; \
    pg_restore --version
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen
COPY . .
EXPOSE 8000
CMD ["uv", "run", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]

# ---- prod: 소스 복사 + 비루트 유저. 테스트/개발 의존성 없음 ----
FROM base AS prod
COPY --from=deps /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH"
COPY ./app ./app
# 시드 데이터(타입 상성표, 기술 수치, 한영 매핑). 계산 엔진이 읽으므로 운영 이미지에 필수.
COPY ./data ./data
# ★ 마이그레이션을 운영 이미지가 들고 있어야 한다 ★
# 배포 환경에서 스키마를 올리는 주체가 이 이미지다 (ECS 의 일회성 태스크 등).
# 빠뜨리면 "이미지는 뜨는데 스키마가 없다" 가 되고, 그건 첫 배포에서 터진다.
# 실제로 "Phase 2 에서 되살린다" 는 주석만 남은 채 Phase 2 가 끝나 있었다.
COPY ./alembic ./alembic
COPY alembic.ini .
# 시드 적재 스크립트. 빈 DB 에 마스터 데이터를 넣는 것도 배포 절차의 일부다.
COPY ./scripts ./scripts
# ★ 홈 디렉토리를 만든다 ★
# `--no-create-home` 이면 gunicorn 23 의 control server 가 부팅마다
#   [ERROR] Control server error: [Errno 13] Permission denied: '/home/appuser'
# 를 남긴다. 서비스는 돌지만 **평소에 에러가 쌓이면 진짜 문제를 로그에서
# 구분할 수 없다.** 익스포터 WAL 콜렉터 건과 같은 종류다 (JOURNAL 2026-11-17).
RUN adduser --disabled-password --gecos "" appuser && chown -R appuser /app /home/appuser
USER appuser
EXPOSE 8000
CMD ["gunicorn", "app.main:app", "-k", "uvicorn.workers.UvicornWorker", \
     "-w", "2", "-b", "0.0.0.0:8000"]
