"""요청 예절 게이트 단위 테스트 (CLAUDE.md 8-6, 가이드 4-4 체크리스트).

★ 이 테스트가 막는 것: 출처에 과도한 부하를 주는 것 ★

championsbattledata 는 고정 rate limit 이 없다. **막아주는 쪽이 없으니 우리가
지켜야 하고, 지키는지 확인하는 수단은 테스트뿐이다.** 상수를 선언해두는 것만으로는
지켜지지 않는다 — 호출 경로가 그 상수를 실제로 통과하는지가 별개 문제다.

실제로 기다리지 않는다. 시계와 sleep 을 주입해 가짜로 만든다.
1 초를 진짜로 기다리는 테스트는 느려서 결국 아무도 돌리지 않는다.
"""

from __future__ import annotations

import asyncio

import pytest

from app.ingestion.gate import RequestGate


class FakeClock:
    """단조시계 대역. 흐르지 않고, 가짜 sleep 이 밀어줄 때만 움직인다."""

    def __init__(self, now: float = 0.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


class RecordingSleep:
    """기다린 시간을 기록하고, 그만큼 시계를 민다."""

    def __init__(self, clock: FakeClock) -> None:
        self.clock = clock
        self.waits: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.waits.append(seconds)
        self.clock.now += seconds


def _gate(min_interval: float = 1.0) -> tuple[RequestGate, FakeClock, RecordingSleep]:
    clock = FakeClock()
    sleep = RecordingSleep(clock)
    return RequestGate(min_interval=min_interval, clock=clock, sleep=sleep), clock, sleep


class TestFirstRequest:
    """경계값 분석 — '이전 요청이 없다'는 경계. 여기서 기다리면 매 배치가 1초씩 느려진다."""

    async def test_first_request_does_not_wait(self):
        gate, _clock, sleep = _gate()
        async with gate:
            pass
        assert sleep.waits == []


class TestInterval:
    """경계값 분석 — 최소 간격의 미달/일치/초과 세 구간을 경계에서 집는다.

    이 검증이 막는 것: 간격 계산이 어긋나 출처에 1초보다 빠르게 연속 요청하는 것.
    """

    async def test_waits_only_the_remaining_time(self):
        """0.3 초가 지났으면 0.7 초만 더 기다린다. 1 초를 또 기다리면 두 배로 느리다."""
        gate, clock, sleep = _gate(1.0)
        async with gate:
            pass
        clock.now += 0.3
        async with gate:
            pass
        assert sleep.waits == [pytest.approx(0.7)]

    async def test_exactly_at_the_interval_does_not_wait(self):
        """★ 경계 그 자체. elapsed == min_interval 은 '충분히 기다렸다' 쪽이다."""
        gate, clock, sleep = _gate(1.0)
        async with gate:
            pass
        clock.now += 1.0
        async with gate:
            pass
        assert sleep.waits == []

    async def test_just_below_the_interval_waits(self):
        """경계 바로 아래. 위 테스트와 쌍으로 놓아야 경계가 어느 쪽인지 고정된다."""
        gate, clock, sleep = _gate(1.0)
        async with gate:
            pass
        clock.now += 0.999
        async with gate:
            pass
        assert sleep.waits == [pytest.approx(0.001)]

    async def test_long_gap_does_not_wait(self):
        gate, clock, sleep = _gate(1.0)
        async with gate:
            pass
        clock.now += 30.0
        async with gate:
            pass
        assert sleep.waits == []

    async def test_a_slow_response_does_not_excuse_the_gap(self):
        """차등 단정 — '끝난 시각 기준'과 '시작한 시각 기준'을 구분하는 유일한 케이스.

        응답에 걸린 시간은 조용한 간격이 **아니다.** 그 2 초 동안 출처는 우리 요청을
        처리하고 있었다. 그래서 응답이 끝난 시각부터 다시 1 초를 센다.

        시작 시각 기준(= 초당 1요청)이면 여기서 기다리지 않는다. CLAUDE.md 8-6 의
        "요청 간 최소 1초 지연"은 그쪽이 아니다. 다른 테스트는 두 기준에서 같은
        결과를 내므로, 기준이 뒤집히면 **이 테스트만** 깨진다.
        """
        gate, clock, sleep = _gate(1.0)
        async with gate:
            clock.now += 2.0  # 응답이 2 초 걸렸다
        async with gate:
            pass
        assert sleep.waits == [pytest.approx(1.0)]


class TestSerialization:
    """동시에 밀어넣어도 요청이 겹치지 않는지 — 동시성 1 (CLAUDE.md 8-6).

    표준 기법명을 붙이지 않는다. 확인하는 것은 '게이트 안에 동시에 둘이 들어오지
    못한다'는 성질 하나다.

    이 검증이 막는 것: 나중에 누가 `asyncio.gather` 로 바꿔 수집을 병렬화하는 것.
    현재 코드가 순차 루프라서 우연히 지켜지는 상태라면, 그 우연은 리팩터링 한 번에
    사라진다.
    """

    async def test_requests_never_overlap(self):
        gate, _clock, _sleep = _gate(0.0)
        live = 0
        peak = 0

        async def request() -> None:
            nonlocal live, peak
            async with gate:
                live += 1
                peak = max(peak, live)
                for _ in range(3):
                    await asyncio.sleep(0)  # 다른 코루틴에 양보한다
                live -= 1

        await asyncio.gather(*(request() for _ in range(10)))
        assert peak == 1

    async def test_interval_is_applied_between_queued_requests(self):
        """줄 서서 들어와도 각 쌍 사이에 간격이 들어간다."""
        gate, _clock, sleep = _gate(1.0)

        async def request() -> None:
            async with gate:
                await asyncio.sleep(0)

        await asyncio.gather(*(request() for _ in range(4)))
        assert sleep.waits == [pytest.approx(1.0)] * 3


class TestFailurePaths:
    """오류 추정 — 실패 경로에서 게이트가 잠긴 채 남는 것이 가장 위험하다.

    이 검증이 막는 것: 한 요청이 예외로 끝난 뒤 수집 전체가 영구히 멈추는 것.
    스케줄러는 사람이 보지 않는 시각에 돈다.
    """

    async def test_lock_is_released_when_the_request_raises(self):
        gate, _clock, _sleep = _gate(0.0)
        with pytest.raises(RuntimeError):
            async with gate:
                raise RuntimeError("요청 실패")

        # 다음 요청이 들어갈 수 있어야 한다. 막히면 여기서 멈춘다.
        await asyncio.wait_for(_enter_and_exit(gate), timeout=1.0)

    async def test_failed_request_still_counts_as_a_request(self):
        """실패해도 출처를 때린 건 사실이다. 간격 기준을 갱신하지 않으면
        실패가 연속될 때 재시도 폭풍이 된다."""
        gate, _clock, sleep = _gate(1.0)
        with pytest.raises(RuntimeError):
            async with gate:
                raise RuntimeError("요청 실패")
        async with gate:
            pass
        assert sleep.waits == [pytest.approx(1.0)]

    async def test_negative_interval_is_rejected(self):
        with pytest.raises(ValueError):
            RequestGate(min_interval=-0.1)


async def _enter_and_exit(gate: RequestGate) -> None:
    async with gate:
        pass
