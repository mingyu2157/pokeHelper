"""요청 속도 제한.

★ 왜 앱 안에 두는가 ★

ALB·WAF 로도 막을 수 있지만 그건 **배포 환경에만 존재해서 테스트할 수 없다.**
이 프로젝트의 규칙은 전부 테스트가 집행한다 (출처 표기·DB 권한·예외 계층·
경보 지표명). 검증할 수 없는 곳에 두면 그 원칙이 깨진다.
엔드포인트별로 다른 한도를 줄 수 있다는 것도 앱 쪽의 장점이다 —
`ev-tune` 은 계산이 무겁고 `/health` 는 그렇지 않다.

★ 저장소가 둘인 이유 ★

메모리 저장소는 **프로세스당** 센다. gunicorn 워커가 2개면 실효 한도가 2배가
되고, ECS 태스크가 늘면 더 벌어진다. 로컬에서는 그걸로 충분하지만 운영에서는
틀린 숫자다. 그래서 Redis 저장소를 같이 둔다.

★ Redis 가 죽으면 통과시킨다 (fail-open) ★

막는 쪽(fail-closed)을 고르면 Redis 가 잠깐 흔들릴 때 **API 전체가 멈춘다.**
속도 제한은 가용성을 지키려고 있는 장치인데 그것 때문에 가용성을 잃는 건
거꾸로다. 대신 **조용히 넘어가지 않는다** — 실패를 카운터로 세고 로그를 남긴다
(`pokehelper_rate_limit_store_failures_total`). "통과시켰다"와 "제한이 동작했다"를
구분할 수 있어야 한다.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Final, Protocol

from prometheus_client import Counter

logger = logging.getLogger(__name__)

#: 제한에 걸려 거부한 요청. 한도가 너무 빡센지 판단하는 근거가 된다.
RATE_LIMIT_REJECTED = Counter(
    "pokehelper_rate_limit_rejected_total",
    "속도 제한으로 거부한 요청 수",
    labelnames=("scope",),
)

#: 저장소 조회 실패. 0 이 아니면 **제한이 실제로는 걸리지 않고 있다.**
RATE_LIMIT_STORE_FAILURES = Counter(
    "pokehelper_rate_limit_store_failures_total",
    "속도 제한 저장소 조회 실패 수 (실패 시 요청을 통과시킨다)",
)


@dataclass(frozen=True)
class Limit:
    """창(window) 안에 허용하는 요청 수."""

    requests: int
    window_seconds: int

    def __post_init__(self) -> None:
        if self.requests < 1:
            raise ValueError("requests 는 1 이상이어야 한다")
        if self.window_seconds < 1:
            raise ValueError("window_seconds 는 1 이상이어야 한다")


@dataclass(frozen=True)
class Decision:
    """한 번의 판정 결과."""

    allowed: bool
    remaining: int
    retry_after_seconds: int


class LimitStore(Protocol):
    """고정 창 카운터. 키 하나를 올리고 현재 값을 돌려준다."""

    async def hit(self, key: str, limit: Limit) -> int:
        """키를 1 올리고 **현재 창의 누적 횟수**를 돌려준다."""
        ...


class MemoryStore:
    """프로세스 메모리 고정 창.

    ★ 프로세스당 센다 ★ 워커가 여럿이면 실효 한도가 그만큼 곱해진다.
    로컬·테스트용이고, 운영에서는 `RedisStore` 를 쓴다.
    """

    def __init__(self) -> None:
        self._hits: dict[str, tuple[int, float]] = {}

    async def hit(self, key: str, limit: Limit) -> int:
        now = time.monotonic()
        count, expires_at = self._hits.get(key, (0, 0.0))
        if now >= expires_at:
            count, expires_at = 0, now + limit.window_seconds
        count += 1
        self._hits[key] = (count, expires_at)
        return count

    def clear(self) -> None:
        """테스트가 창을 끊을 때 쓴다."""
        self._hits.clear()


class RedisStore:
    """Redis 고정 창. 여러 워커·태스크가 같은 카운터를 본다.

    `INCR` 로 올리고 **처음 올린 경우에만** 만료를 건다. 매번 `EXPIRE` 를 걸면
    요청이 들어올 때마다 창이 뒤로 밀려서 영원히 만료되지 않는다.
    """

    def __init__(self, client: object) -> None:
        self._client = client

    async def hit(self, key: str, limit: Limit) -> int:
        pipeline = self._client.pipeline()  # type: ignore[attr-defined]
        pipeline.incr(key)
        pipeline.expire(key, limit.window_seconds, nx=True)
        count, _ = await pipeline.execute()
        return int(count)


class RateLimiter:
    """저장소를 받아 판정만 한다. HTTP 를 모른다 — 그래서 테스트하기 쉽다."""

    def __init__(self, store: LimitStore) -> None:
        self._store = store

    async def check(self, *, scope: str, identity: str, limit: Limit) -> Decision:
        key = f"ratelimit:{scope}:{identity}"
        try:
            count = await self._store.hit(key, limit)
        # 저장소 장애는 우리 버그가 아니다. 여기서만 넓게 잡고 **반드시 센다**.
        except Exception as exc:  # noqa: BLE001
            RATE_LIMIT_STORE_FAILURES.inc()
            logger.warning("속도 제한 저장소 조회 실패 — 요청을 통과시킨다: %s", exc)
            return Decision(allowed=True, remaining=limit.requests, retry_after_seconds=0)

        if count > limit.requests:
            RATE_LIMIT_REJECTED.labels(scope=scope).inc()
            return Decision(allowed=False, remaining=0, retry_after_seconds=limit.window_seconds)
        return Decision(allowed=True, remaining=limit.requests - count, retry_after_seconds=0)


#: 프록시 뒤에서 실제 클라이언트를 찾을 때 보는 헤더.
FORWARDED_FOR: Final[str] = "x-forwarded-for"


def client_identity(
    *, client_host: str | None, forwarded_for: str | None, trust_proxy: bool
) -> str:
    """제한을 걸 대상 식별자.

    ★ `X-Forwarded-For` 를 조건 없이 믿지 않는다 ★

    그 헤더는 클라이언트가 **마음대로 적어 보낼 수 있다.** 프록시 뒤가 아닌데
    믿으면, 공격자가 매 요청마다 다른 값을 넣어 제한을 통째로 우회한다.
    ALB 뒤에 있을 때만(`trust_proxy`) 보고, 그때도 **맨 앞 항목**을 쓴다.
    """
    if trust_proxy and forwarded_for:
        first = forwarded_for.split(",")[0].strip()
        if first:
            return first
    return client_host or "unknown"
