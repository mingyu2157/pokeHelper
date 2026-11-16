"""요청 예절 게이트 (CLAUDE.md 8-6, 가이드 4-4 체크리스트).

championsbattledata 는 고정 rate limit 이 없다. **막아주는 쪽이 없으므로 우리가
지킨다** — 요청 간 최소 1초, 동시성 1.

★ 왜 상수와 `sleep` 호출로 끝내지 않는가 ★

상수를 선언하고 루프 안에서 `sleep` 을 부르는 방식은 "어느 루프 안에서" 지키는지가
코드 모양에 달려 있다. 루프가 둘로 갈리거나 앞에 다른 요청이 하나 끼면 그 사이는
0 초가 된다. 게이트로 만들면 **모든 요청이 같은 문을 지나가므로** 호출 구조가
바뀌어도 간격이 유지된다.

동시성 1 도 마찬가지다. 순차 `for` 루프는 동시성 1 을 *우연히* 만족한다.
그 우연은 누군가 `asyncio.gather` 로 바꾸는 순간 사라지고, 아무것도 막지 않는다.
락으로 직렬화하면 호출자가 무엇을 하든 요청은 겹치지 않는다.

시계와 `sleep` 을 주입받는다. 테스트가 실제로 1초를 기다리지 않게 하기 위해서다.
느린 테스트는 결국 아무도 돌리지 않는다.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from types import TracebackType

Clock = Callable[[], float]
Sleeper = Callable[[float], Awaitable[None]]


class RequestGate:
    """요청을 직렬화하고 요청 사이에 최소 간격을 둔다.

    `async with gate:` 블록 안에서 요청 하나를 보낸다. 블록이 열릴 때까지
    기다리고, 닫힐 때 "마지막 요청이 끝난 시각"을 갱신한다.

    간격은 **이전 요청이 끝난 시각부터** 잰다.

    ★ 끝난 시각이냐 시작한 시각이냐 — 느린 응답에서 답이 갈린다 ★

    응답에 걸린 시간은 **조용한 간격이 아니다.** 그 2초 동안 출처는 우리 요청을
    처리하고 있었다. 공백이 아니라 그 반대다.

    | 기준 | 의미 | 응답이 2초 걸린 뒤 |
    |---|---|---|
    | 끝난 시각 (채택) | 요청 사이에 1초의 정적 | 1초 더 기다린다 |
    | 시작한 시각 | 초당 1요청 (rate limit) | 즉시 다음 요청 |

    CLAUDE.md 8-6 은 "요청 **간** 최소 1초 **지연**"이라고 쓰고, "고정 rate limit 이
    없다고 몰아치지 않는다"를 덧붙인다. 둘 다 보수적인 쪽을 가리키므로 끝난 시각을
    기준으로 삼는다.
    """

    def __init__(
        self,
        *,
        min_interval: float,
        clock: Clock = time.monotonic,
        sleep: Sleeper = asyncio.sleep,
    ) -> None:
        if min_interval < 0:
            raise ValueError(f"min_interval 은 0 이상이어야 한다: {min_interval}")
        self._min_interval = min_interval
        self._clock = clock
        self._sleep = sleep
        self._lock = asyncio.Lock()
        # 이전 요청이 끝난 시각. 아직 아무 요청도 없으면 None 이고, 그때는 기다리지 않는다.
        self._last_finished: float | None = None

    @property
    def min_interval(self) -> float:
        return self._min_interval

    async def wait(self, seconds: float) -> None:
        """게이트와 **같은 시계**로 기다린다.

        재시도 백오프처럼 게이트 밖에서 쉬어야 할 때 쓴다. `asyncio.sleep` 을
        직접 부르면 그 대기만 테스트에서 가짜로 만들 수 없게 된다.
        """
        if seconds > 0:
            await self._sleep(seconds)

    async def __aenter__(self) -> None:
        await self._lock.acquire()
        try:
            if self._last_finished is not None:
                remaining = self._min_interval - (self._clock() - self._last_finished)
                if remaining > 0:
                    await self._sleep(remaining)
        except BaseException:
            # 기다리는 중에 취소되면 락을 들고 죽는다. 그러면 수집 전체가 영구히 멈춘다.
            self._lock.release()
            raise

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        # ★ 실패한 요청도 요청이다. 출처를 때린 건 사실이므로 간격 기준을 갱신한다.
        #   갱신하지 않으면 실패가 연속될 때 재시도 폭풍이 된다.
        self._last_finished = self._clock()
        self._lock.release()
