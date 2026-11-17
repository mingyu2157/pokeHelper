"""애플리케이션 설정.

환경변수는 여기서만 읽는다. domain 계층은 이 모듈을 import 하지 않는다.
"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "development"
    log_level: str = "INFO"

    # ★ 앱 런타임 계정. DML 만 가진다 — DDL 권한이 없다 (ADR-015).
    # 기본값을 최소 권한 계정으로 둔다. 부트스트랩 계정(POSTGRES_USER)을 기본값으로
    # 두면 .env 를 안 만든 환경이 조용히 슈퍼유저로 돌아간다.
    database_url: str = "postgresql+asyncpg://champs_app:devapp@db:5432/champs"
    # ★ 마이그레이션 전용 계정. DDL 을 가진 유일한 계정이고 alembic 만 쓴다.
    # 런타임 코드가 이 값을 읽으면 분리의 의미가 없어진다 — alembic/env.py 만 읽는다.
    migration_database_url: str = "postgresql+asyncpg://champs_migrate:devmigrate@db:5432/champs"
    redis_url: str = "redis://redis:6379/0"

    # ── 관측 지표 (ADR-016) ────────────────────────────────────────
    # /metrics 는 스크랩 때 DB 를 한 번 읽는다. 두 숫자가 그 비용을 가둔다.
    #   cache: 스크랩이 잦아도 이 간격 안에서는 DB 를 때리지 않는다.
    #   timeout: DB 가 느릴 때 /metrics 가 같이 느려지는 것을 끊는다.
    #            끊기면 직전 값을 유지한다 (0 으로 떨구지 않는다).
    metrics_cache_seconds: float = 15.0
    metrics_db_timeout_seconds: float = 2.0

    # 브라우저가 백엔드를 직접 호출하므로 프론트 오리진을 허용해야 한다.
    cors_origins: str = "http://localhost:3000"

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
