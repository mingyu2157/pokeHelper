"""배포 선결 조건이 실제로 집행되는가 (ADR-019).

★ 이 파일이 막는 것 ★

셋 다 **열린 채로 배포해도 아무것도 깨지지 않는다.** 서비스는 멀쩡히 응답하고
화면도 정상이다. 그래서 사람이 알아채지 못하고, 테스트가 막아야 한다.

- `/metrics` 가 열리면 경로별 지연·오류율이 공개된다
- 속도 제한이 없으면 계산 엔드포인트가 무방비다
- 운영 환경에서 기본값이 남아 있으면 위 둘이 동시에 일어난다

`db` 마커 없음 — DB 를 보지 않는 경로만 쓴다.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import deps
from app.core.config import (
    InsecureProductionConfigError,
    Settings,
    assert_production_ready,
    get_settings,
)
from app.core.ratelimit import MemoryStore


@pytest.fixture(autouse=True)
def fresh_limiter():
    """테스트마다 카운터를 비운다. 앞 테스트가 쓴 횟수가 넘어오면 안 된다."""
    deps.reset_limiter_store()
    get_settings.cache_clear()
    yield
    deps.reset_limiter_store()
    get_settings.cache_clear()


def build_app(**overrides) -> FastAPI:
    """설정을 주입한 앱. 환경변수를 건드리지 않는다."""
    from app.main import app

    settings = Settings(**overrides)
    app.dependency_overrides[get_settings] = lambda: settings
    return app


class TestMetricsToken:
    """★ `/metrics` 는 내부 정보다 (ADR-016 → ADR-019)."""

    def test_open_when_no_token_is_configured(self) -> None:
        """로컬 compose 의 Prometheus 가 토큰 없이 긁을 수 있어야 한다."""
        app = build_app(metrics_token="")
        try:
            with TestClient(app) as client:
                assert client.get("/metrics").status_code == 200
        finally:
            app.dependency_overrides.clear()

    def test_rejected_without_a_token(self) -> None:
        app = build_app(metrics_token="s3cret")
        try:
            with TestClient(app) as client:
                response = client.get("/metrics")
                assert response.status_code == 401
                assert response.headers.get("www-authenticate") == "Bearer"
        finally:
            app.dependency_overrides.clear()

    def test_rejected_with_a_wrong_token(self) -> None:
        app = build_app(metrics_token="s3cret")
        try:
            with TestClient(app) as client:
                response = client.get("/metrics", headers={"Authorization": "Bearer nope"})
                assert response.status_code == 401
        finally:
            app.dependency_overrides.clear()

    def test_accepted_with_the_right_token(self) -> None:
        app = build_app(metrics_token="s3cret")
        try:
            with TestClient(app) as client:
                response = client.get("/metrics", headers={"Authorization": "Bearer s3cret"})
                assert response.status_code == 200
                assert "pokehelper_" in response.text
        finally:
            app.dependency_overrides.clear()

    def test_a_bare_token_without_the_scheme_is_rejected(self) -> None:
        """`Authorization: s3cret` 처럼 스킴 없이 보내면 거부한다."""
        app = build_app(metrics_token="s3cret")
        try:
            with TestClient(app) as client:
                assert client.get("/metrics", headers={"Authorization": "s3cret"}).status_code == (
                    401
                )
        finally:
            app.dependency_overrides.clear()


class TestRateLimitIsWired:
    """한도가 **라우터에 실제로 붙어 있는가**.

    판정기 단위 테스트(`test_ratelimit.py`)가 전부 통과해도, 라우터에 의존성을
    달지 않으면 아무것도 제한되지 않는다. 정의와 적용은 별개의 사실이다.
    """

    def test_calc_endpoint_rejects_past_its_limit(self) -> None:
        app = build_app(rate_limit_calc_requests=3, rate_limit_calc_window_seconds=60)
        body = {
            "base_stats": {
                "hp": 108,
                "attack": 130,
                "defense": 95,
                "sp_attack": 80,
                "sp_defense": 85,
                "speed": 102,
            },
            "types": ["dragon", "ground"],
            "constraints": [],
        }
        try:
            with TestClient(app) as client:
                codes = [
                    client.post("/api/v1/calc/ev-tune", json=body).status_code for _ in range(5)
                ]
            assert codes.count(200) == 3, codes
            assert codes.count(429) == 2, codes
        finally:
            app.dependency_overrides.clear()

    def test_rejection_tells_the_caller_when_to_retry(self) -> None:
        """429 만 주고 숫자가 없으면 호출자는 바로 재시도한다."""
        app = build_app(rate_limit_calc_requests=1, rate_limit_calc_window_seconds=42)
        body = {
            "base_stats": {
                "hp": 108,
                "attack": 130,
                "defense": 95,
                "sp_attack": 80,
                "sp_defense": 85,
                "speed": 102,
            },
            "types": ["dragon", "ground"],
            "constraints": [],
        }
        try:
            with TestClient(app) as client:
                client.post("/api/v1/calc/ev-tune", json=body)
                blocked = client.post("/api/v1/calc/ev-tune", json=body)
            assert blocked.status_code == 429
            assert blocked.headers["retry-after"] == "42"
        finally:
            app.dependency_overrides.clear()

    def test_health_is_not_rate_limited(self) -> None:
        """로드밸런서가 때리는 경로다. 여기에 한도를 걸면 스스로 내려간다."""
        app = build_app(rate_limit_default_requests=2, rate_limit_calc_requests=2)
        try:
            with TestClient(app) as client:
                codes = [client.get("/health").status_code for _ in range(6)]
            assert set(codes) == {200}, codes
        finally:
            app.dependency_overrides.clear()

    def test_disabling_the_limit_lets_everything_through(self) -> None:
        app = build_app(rate_limit_enabled=False, rate_limit_calc_requests=1)
        body = {
            "base_stats": {
                "hp": 108,
                "attack": 130,
                "defense": 95,
                "sp_attack": 80,
                "sp_defense": 85,
                "speed": 102,
            },
            "types": ["dragon", "ground"],
            "constraints": [],
        }
        try:
            with TestClient(app) as client:
                codes = [
                    client.post("/api/v1/calc/ev-tune", json=body).status_code for _ in range(3)
                ]
            assert set(codes) == {200}, codes
        finally:
            app.dependency_overrides.clear()


class TestProductionGuard:
    """★ 운영으로 뜨려는데 열려 있으면 **기동을 거부한다**.

    경고 로그로 끝내면 아무도 안 본다. 배포 파이프라인이 멈춰야 사람이 본다.
    """

    def test_development_is_not_checked(self) -> None:
        """로컬은 기본값으로 떠야 한다. 개발을 막으면 우회법이 생긴다."""
        assert_production_ready(Settings(app_env="development"))

    @pytest.mark.parametrize(
        ("overrides", "expected"),
        [
            ({"metrics_token": ""}, "METRICS_TOKEN"),
            ({"rate_limit_backend": "memory"}, "RATE_LIMIT_BACKEND"),
            ({"cors_origins": "http://localhost:3000"}, "CORS_ORIGINS"),
        ],
    )
    def test_each_unsafe_setting_blocks_startup(
        self, overrides: dict[str, object], expected: str
    ) -> None:
        safe = {
            "app_env": "production",
            "metrics_token": "token",
            "rate_limit_backend": "redis",
            "cors_origins": "https://pokehelper.example",
        }
        with pytest.raises(InsecureProductionConfigError) as caught:
            assert_production_ready(Settings(**{**safe, **overrides}))
        assert expected in str(caught.value)

    def test_a_fully_configured_production_starts(self) -> None:
        assert_production_ready(
            Settings(
                app_env="production",
                metrics_token="token",
                rate_limit_backend="redis",
                cors_origins="https://pokehelper.example",
            )
        )

    def test_memory_backend_is_allowed_when_limiting_is_off(self) -> None:
        """제한을 끄면 저장소 종류는 상관없다. 과하게 막지 않는다."""
        assert_production_ready(
            Settings(
                app_env="production",
                metrics_token="token",
                rate_limit_enabled=False,
                rate_limit_backend="memory",
                cors_origins="https://pokehelper.example",
            )
        )


class TestMemoryStoreIsPerProcess:
    """★ 메모리 저장소의 한계를 테스트가 기억한다.

    운영에서 Redis 를 쓰는 이유가 이것이다. 나중에 "메모리로 충분하지 않나"
    라는 질문이 나오면 이 테스트가 답한다.
    """

    async def test_two_stores_do_not_share_a_counter(self) -> None:
        from app.core.ratelimit import Limit

        first, second = MemoryStore(), MemoryStore()
        limit = Limit(requests=10, window_seconds=60)
        assert await first.hit("same-key", limit) == 1
        assert await second.hit("same-key", limit) == 1, (
            "워커마다 별도로 센다 — 실효 한도가 워커 수만큼 곱해진다"
        )
