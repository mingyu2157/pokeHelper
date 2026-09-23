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
# Phase 2 에서 alembic 도입 시 아래 두 줄을 되살린다.
# COPY ./alembic ./alembic
# COPY alembic.ini .
RUN adduser --disabled-password --no-create-home appuser && chown -R appuser /app
USER appuser
EXPOSE 8000
CMD ["gunicorn", "app.main:app", "-k", "uvicorn.workers.UvicornWorker", \
     "-w", "2", "-b", "0.0.0.0:8000"]
