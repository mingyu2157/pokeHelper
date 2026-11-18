"""API 공통 의존성 — 속도 제한과 /metrics 접근 통제 (ADR-019).

라우터는 "어떤 한도인지"만 선언하고 판정은 `core/ratelimit.py` 가 한다.
HTTP 를 아는 쪽과 세는 쪽을 나눠야 세는 쪽을 테스트하기 쉽다.
"""

from __future__ import annotations

import secrets
from typing import Final

from fastapi import Depends, HTTPException, Request, status
from redis.asyncio import Redis

from app.core.config import Settings, get_settings
from app.core.ratelimit import (
    Limit,
    LimitStore,
    MemoryStore,
    RateLimiter,
    RedisStore,
    client_identity,
)

#: 저장소는 프로세스당 하나다. 메모리 저장소는 상태를 들고 있어야 하고,
#: Redis 커넥션 풀도 요청마다 새로 만들면 안 된다.
_store: LimitStore | None = None


def _build_store(settings: Settings) -> LimitStore:
    if settings.rate_limit_backend == "redis":
        return RedisStore(Redis.from_url(settings.redis_url, decode_responses=True))
    return MemoryStore()


def get_limiter() -> RateLimiter:
    global _store
    if _store is None:
        _store = _build_store(get_settings())
    return RateLimiter(_store)


def reset_limiter_store() -> None:
    """테스트가 저장소를 갈아끼울 때 쓴다. 운영 경로에서는 부르지 않는다."""
    global _store
    _store = None


class RateLimit:
    """라우터에 붙이는 의존성. 범위(scope)마다 한도가 다르다.

    사용: `dependencies=[Depends(RateLimit("calc"))]`
    """

    def __init__(self, scope: str) -> None:
        self.scope = scope

    def _limit(self, settings: Settings) -> Limit:
        if self.scope == "calc":
            return Limit(
                requests=settings.rate_limit_calc_requests,
                window_seconds=settings.rate_limit_calc_window_seconds,
            )
        return Limit(
            requests=settings.rate_limit_default_requests,
            window_seconds=settings.rate_limit_default_window_seconds,
        )

    async def __call__(
        self,
        request: Request,
        settings: Settings = Depends(get_settings),  # noqa: B008
        limiter: RateLimiter = Depends(get_limiter),  # noqa: B008
    ) -> None:
        if not settings.rate_limit_enabled:
            return

        identity = client_identity(
            client_host=request.client.host if request.client else None,
            forwarded_for=request.headers.get("x-forwarded-for"),
            trust_proxy=settings.trust_proxy_headers,
        )
        decision = await limiter.check(
            scope=self.scope, identity=identity, limit=self._limit(settings)
        )
        if not decision.allowed:
            # 429 와 Retry-After 를 함께 준다. 숫자 없이 거부만 하면 호출자는
            # 언제 다시 와야 하는지 모르고 바로 재시도한다.
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="요청이 너무 잦다. 잠시 후 다시 시도해라.",
                headers={"Retry-After": str(decision.retry_after_seconds)},
            )


BEARER_PREFIX: Final[str] = "Bearer "


def require_metrics_token(
    request: Request,
    settings: Settings = Depends(get_settings),  # noqa: B008
) -> None:
    """`/metrics` 접근 통제 (ADR-016 → ADR-019).

    ★ 토큰이 비어 있으면 통과시킨다 ★ 로컬 compose 에서 Prometheus 가 토큰
    없이 긁을 수 있어야 한다. 대신 **운영에서 비어 있으면 앱이 아예 뜨지
    않는다** (`assert_production_ready`). "기본값이 열림"인 설정은 반드시
    기동 시점에 막아야 배포에서 조용히 열린 채로 나가지 않는다.

    비교는 `secrets.compare_digest` 로 한다. `==` 는 앞에서부터 비교하다
    다른 글자를 만나면 바로 끝나서, 응답 시간으로 토큰을 한 글자씩
    알아낼 수 있다.
    """
    expected = settings.metrics_token
    if not expected:
        return

    header = request.headers.get("authorization", "")
    provided = header[len(BEARER_PREFIX) :] if header.startswith(BEARER_PREFIX) else ""
    if not provided or not secrets.compare_digest(provided, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="metrics 토큰이 필요하다",
            headers={"WWW-Authenticate": "Bearer"},
        )
