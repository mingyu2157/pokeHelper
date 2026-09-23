"""애플리케이션 설정.

환경변수는 여기서만 읽는다. domain 계층은 이 모듈을 import 하지 않는다.
"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "development"
    log_level: str = "INFO"

    # Phase 2 에서 실제 연결에 사용한다. Phase 0 에서는 값만 받아둔다.
    database_url: str = "postgresql+asyncpg://champs:devpassword@db:5432/champs"
    redis_url: str = "redis://redis:6379/0"

    # 브라우저가 백엔드를 직접 호출하므로 프론트 오리진을 허용해야 한다.
    cors_origins: str = "http://localhost:3000"

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
