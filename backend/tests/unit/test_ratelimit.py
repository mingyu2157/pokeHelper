"""속도 제한 단위 테스트 (ADR-019).

판정기는 HTTP 를 모른다. 저장소를 끼워 넣으면 **시계도 네트워크도 없이**
경계를 전부 집을 수 있다 — 앱 안에 둔 이유가 이것이다. ALB·WAF 였다면
이 테스트는 존재할 수 없다.
"""

from __future__ import annotations

import pytest

from app.core.ratelimit import (
    Decision,
    Limit,
    MemoryStore,
    RateLimiter,
    client_identity,
)


class CountingStore:
    """호출 횟수만 세는 가짜 저장소. 창 만료를 다루지 않는다."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, Limit]] = []
        self.counts: dict[str, int] = {}

    async def hit(self, key: str, limit: Limit) -> int:
        self.calls.append((key, limit))
        self.counts[key] = self.counts.get(key, 0) + 1
        return self.counts[key]


class BrokenStore:
    """항상 터지는 저장소. Redis 장애를 흉내낸다."""

    async def hit(self, key: str, limit: Limit) -> int:
        raise ConnectionError("redis 가 죽었다")


class TestLimitValidation:
    """경계값 분석 — 말이 안 되는 한도를 만들 수 없다."""

    @pytest.mark.parametrize(("requests", "window"), [(0, 60), (-1, 60), (10, 0), (10, -1)])
    def test_invalid_limits_are_rejected(self, requests: int, window: int) -> None:
        with pytest.raises(ValueError):
            Limit(requests=requests, window_seconds=window)

    def test_smallest_valid_limit_is_allowed(self) -> None:
        """1회/1초는 유효하다. 하한을 금지선과 혼동하지 않는다."""
        assert Limit(requests=1, window_seconds=1).requests == 1


class TestAllowsUpToTheLimit:
    """경계값 분석 — 한도 **직전·정확히·직후** 를 집는다."""

    async def test_requests_up_to_the_limit_pass(self) -> None:
        limiter = RateLimiter(CountingStore())
        limit = Limit(requests=3, window_seconds=60)
        results = [
            await limiter.check(scope="calc", identity="1.1.1.1", limit=limit) for _ in range(3)
        ]
        assert all(r.allowed for r in results), results

    async def test_the_request_after_the_limit_is_rejected(self) -> None:
        limiter = RateLimiter(CountingStore())
        limit = Limit(requests=3, window_seconds=60)
        for _ in range(3):
            await limiter.check(scope="calc", identity="1.1.1.1", limit=limit)
        fourth = await limiter.check(scope="calc", identity="1.1.1.1", limit=limit)
        assert fourth == Decision(allowed=False, remaining=0, retry_after_seconds=60)

    async def test_remaining_counts_down(self) -> None:
        limiter = RateLimiter(CountingStore())
        limit = Limit(requests=3, window_seconds=60)
        remaining = [
            (await limiter.check(scope="calc", identity="1.1.1.1", limit=limit)).remaining
            for _ in range(3)
        ]
        assert remaining == [2, 1, 0]


class TestIdentitiesAreCountedSeparately:
    """한 사람이 많이 쓴다고 다른 사람이 막히면 안 된다."""

    async def test_different_identities_do_not_share_a_budget(self) -> None:
        limiter = RateLimiter(CountingStore())
        limit = Limit(requests=1, window_seconds=60)
        first = await limiter.check(scope="calc", identity="1.1.1.1", limit=limit)
        second = await limiter.check(scope="calc", identity="2.2.2.2", limit=limit)
        assert first.allowed and second.allowed

    async def test_different_scopes_do_not_share_a_budget(self) -> None:
        """읽기를 많이 했다고 계산이 막히면 한도를 따로 둔 의미가 없다."""
        limiter = RateLimiter(CountingStore())
        limit = Limit(requests=1, window_seconds=60)
        read = await limiter.check(scope="read", identity="1.1.1.1", limit=limit)
        calc = await limiter.check(scope="calc", identity="1.1.1.1", limit=limit)
        assert read.allowed and calc.allowed


class TestStoreFailureFailsOpen:
    """★ Redis 가 죽으면 통과시킨다. 다만 조용히 넘어가지 않는다.

    막는 쪽(fail-closed)을 고르면 Redis 가 잠깐 흔들릴 때 API 전체가 멈춘다.
    속도 제한은 가용성을 지키려고 있는 장치인데 그것 때문에 가용성을 잃는 건
    거꾸로다.
    """

    async def test_requests_pass_when_the_store_is_down(self) -> None:
        limiter = RateLimiter(BrokenStore())
        decision = await limiter.check(
            scope="calc", identity="1.1.1.1", limit=Limit(requests=1, window_seconds=60)
        )
        assert decision.allowed

    async def test_the_failure_is_counted(self) -> None:
        """차등 단정 — 통과시키되 **흔적을 남긴다**.

        카운터가 없으면 "제한이 동작했다"와 "저장소가 죽어서 전부 통과했다"를
        구분할 수 없다. 둘은 똑같이 200 을 돌려준다.
        """
        from app.core.ratelimit import RATE_LIMIT_STORE_FAILURES

        before = RATE_LIMIT_STORE_FAILURES._value.get()
        limiter = RateLimiter(BrokenStore())
        await limiter.check(
            scope="calc", identity="1.1.1.1", limit=Limit(requests=1, window_seconds=60)
        )
        assert RATE_LIMIT_STORE_FAILURES._value.get() == before + 1


class TestMemoryStoreWindow:
    """상태 전이 — 창이 지나면 다시 센다."""

    async def test_window_resets_after_it_expires(self) -> None:
        store = MemoryStore()
        limit = Limit(requests=1, window_seconds=1)
        assert await store.hit("k", limit) == 1
        assert await store.hit("k", limit) == 2
        store.clear()  # 창 만료와 같은 효과
        assert await store.hit("k", limit) == 1


class TestClientIdentity:
    """★ X-Forwarded-For 를 조건 없이 믿지 않는다.

    그 헤더는 클라이언트가 **마음대로 적어 보낼 수 있다.** 프록시 뒤가 아닌데
    믿으면, 매 요청마다 다른 값을 넣어 제한을 통째로 우회한다.
    """

    def test_forwarded_header_is_ignored_without_a_proxy(self) -> None:
        identity = client_identity(
            client_host="10.0.0.1", forwarded_for="1.2.3.4", trust_proxy=False
        )
        assert identity == "10.0.0.1", "프록시가 없는데 헤더를 믿으면 제한이 우회된다"

    def test_forwarded_header_is_used_behind_a_proxy(self) -> None:
        identity = client_identity(
            client_host="10.0.0.1", forwarded_for="1.2.3.4", trust_proxy=True
        )
        assert identity == "1.2.3.4"

    def test_first_entry_wins_in_a_chain(self) -> None:
        """프록시가 여러 단이면 **맨 앞**이 원래 클라이언트다."""
        identity = client_identity(
            client_host="10.0.0.1",
            forwarded_for="1.2.3.4, 10.0.0.2, 10.0.0.3",
            trust_proxy=True,
        )
        assert identity == "1.2.3.4"

    def test_missing_client_falls_back_to_a_constant(self) -> None:
        """식별자가 없다고 예외를 내지 않는다. 한 바구니에 모아 센다."""
        assert client_identity(client_host=None, forwarded_for=None, trust_proxy=False) == (
            "unknown"
        )
