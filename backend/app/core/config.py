"""애플리케이션 설정.

환경변수는 여기서만 읽는다. domain 계층은 이 모듈을 import 하지 않는다.
"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.exceptions import ConfigError


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

    # ── 배포 선결 조건 (ADR-019) ──────────────────────────────────
    # ★ /metrics 토큰 ★ 경로별 지연·오류율은 내부 정보다. 비워두면 열려 있고,
    # 운영(app_env=production)에서 비어 있으면 **기동을 거부한다**
    # (`assert_production_ready`). 기본값이 "열림"인 설정은 배포에서
    # 조용히 열린 채로 나간다.
    metrics_token: str = ""

    # ── 요청 속도 제한 ────────────────────────────────────────────
    rate_limit_enabled: bool = True
    # "memory" 는 **프로세스당** 센다. 워커가 여럿이면 실효 한도가 곱해지므로
    # 운영에서는 "redis" 여야 한다. 운영에서 memory 면 기동을 거부한다.
    rate_limit_backend: str = "memory"
    # 일반 읽기 엔드포인트. 화면 한 번 여는 데 몇 건이면 충분하다.
    rate_limit_default_requests: int = 60
    rate_limit_default_window_seconds: int = 60
    # ★ 계산 엔드포인트는 따로 센다 ★ ev-tune 은 제약 8개에서 96ms 를 쓴다.
    # 읽기와 같은 한도를 주면 CPU 를 지키지 못한다.
    rate_limit_calc_requests: int = 20
    rate_limit_calc_window_seconds: int = 60
    # ★ X-Forwarded-For 는 클라이언트가 지어낼 수 있다 ★ ALB 뒤에 있을 때만 켠다.
    # 켜고 프록시가 없으면 속도 제한이 통째로 우회된다.
    trust_proxy_headers: bool = False

    # 브라우저가 백엔드를 직접 호출하므로 프론트 오리진을 허용해야 한다.
    cors_origins: str = "http://localhost:3000"

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def is_production(self) -> bool:
        return self.app_env.lower() in {"production", "prod"}


class InsecureProductionConfigError(ConfigError):
    """운영으로 뜨려는데 안전하지 않은 설정이 남아 있다.

    ★ 기동을 막는다 ★ 경고 로그로 끝내면 아무도 안 본다. 배포 파이프라인이
    **멈춰야** 사람이 본다. 열린 채로 서비스되는 것보다 안 뜨는 게 낫다.
    """


def assert_production_ready(settings: Settings) -> None:
    """운영 환경에서 열려 있으면 안 되는 것들을 확인한다 (ADR-019)."""
    if not settings.is_production:
        return

    problems: list[str] = []
    if not settings.metrics_token:
        problems.append(
            "METRICS_TOKEN 이 비어 있다 — /metrics 가 공개된다. 경로별 지연·오류율은 내부 정보다"
        )
    if settings.rate_limit_enabled and settings.rate_limit_backend != "redis":
        problems.append(
            f"RATE_LIMIT_BACKEND 가 '{settings.rate_limit_backend}' 다 — "
            "메모리 저장소는 프로세스당 세므로 워커 수만큼 한도가 곱해진다"
        )
    if "localhost" in settings.cors_origins:
        problems.append(f"CORS_ORIGINS 에 localhost 가 남아 있다: {settings.cors_origins}")

    if problems:
        joined = "\n  - ".join(problems)
        raise InsecureProductionConfigError(f"운영 설정이 안전하지 않다:\n  - {joined}")


@lru_cache
def get_settings() -> Settings:
    return Settings()
