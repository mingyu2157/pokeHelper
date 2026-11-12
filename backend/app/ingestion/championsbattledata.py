"""championsbattledata.com 공개 API 수집기 (CLAUDE.md 8-1).

스크래핑하지 않는다. 공개 JSON API 를 쓴다.

★ 이 API 는 포켓몬의 '사용률 %' 를 주지 않는다 ★

`/api/rankings` 는 `position`(순위)만 주고, 인덱스의 `battleSummary` 는 카테고리별
1위만 담는다. 그래서 `PokemonUsage.usage_rate` 는 None 으로 남는다.
**0.0 으로 채우지 않는다** — "사용률 0%" 와 "사용률을 모른다" 는 다르고,
화면에는 "사용 순위 2위" 로 표시해야 한다.

채용률(기술·도구·특성·성격·배분)은 퍼센트로 나온다. 다만 파트너(teammate)는
순위만 주고 비율을 주지 않는다.

요청 예절 (CLAUDE.md 8-6): 요청 간 최소 1초, 동시성 1. 고정 rate limit 이 없다고
몰아치지 않는다.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any, Final

import httpx

from app.ingestion.base import CollectionResult, PokemonUsage, StatsSource
from app.ingestion.normalize import normalize_rows

API_ROOT: Final[str] = "https://championsbattledata.com/api"
USER_AGENT: Final[str] = "pokehelper/0.1 (+https://github.com/mingyu2157/pokeHelper)"

REQUEST_DELAY_SECONDS: Final[float] = 1.0
REQUEST_TIMEOUT_SECONDS: Final[float] = 60.0
TRANSIENT_RETRIES: Final[int] = 3
RETRY_BACKOFF_SECONDS: Final[float] = 3.0

SOURCE_NAME: Final[str] = "championsbattledata"


class CollectionError(Exception):
    """수집이 실패했다. 스냅샷을 활성화하지 않는다."""


class ChampionsBattleDataSource(StatsSource):
    """공개 API 어댑터. 사이트별 필드명은 이 클래스 밖으로 나가지 않는다."""

    def __init__(self, *, client: httpx.AsyncClient | None = None) -> None:
        self._client = client

    @property
    def name(self) -> str:
        return SOURCE_NAME

    async def collect(self, *, battle_format: str, limit: int) -> CollectionResult:
        if limit < 1:
            raise CollectionError(f"limit 은 1 이상이어야 한다: {limit}")

        own_client = self._client is None
        client = self._client or httpx.AsyncClient(
            timeout=REQUEST_TIMEOUT_SECONDS,
            headers={"User-Agent": USER_AGENT},
            follow_redirects=True,
        )
        try:
            rankings = await self._get(
                client, "/rankings", params={"format": battle_format, "limit": limit}
            )
            season = str(rankings.get("season") or "Current")
            entries: list[PokemonUsage] = []

            for index, row in enumerate(rankings["rankings"]):
                showdown_id = str(row["showdownId"])
                position = int(row["position"])

                # 요청 사이에 간격을 둔다. 첫 요청 앞에는 두지 않는다.
                if index > 0:
                    await asyncio.sleep(REQUEST_DELAY_SECONDS)

                battle = await self._get(client, f"/battle/{battle_format}/{showdown_id}")
                entries.append(
                    PokemonUsage(
                        showdown_id=showdown_id,
                        rank=position,
                        # ★ 이 API 는 포켓몬 사용률 %를 주지 않는다. 추측하지 않는다.
                        usage_rate=None,
                        raw=battle,
                        **normalize_rows(battle.get("rows", [])),
                    )
                )

            return CollectionResult(
                source=SOURCE_NAME,
                format=battle_format,
                season=season,
                collected_at=datetime.now(UTC),
                entries=tuple(entries),
            )
        finally:
            if own_client:
                await client.aclose()

    async def _get(
        self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """전송 오류만 재시도한다. HTTP 상태 오류는 그대로 올린다.

        데이터가 틀린 것과 네트워크가 끊긴 것은 다르다. 전자는 재시도해도 같고,
        후자는 재시도가 맞다.
        """
        url = f"{API_ROOT}{path}"
        last_error: Exception | None = None
        for attempt in range(TRANSIENT_RETRIES):
            try:
                response = await client.get(url, params=params)
                response.raise_for_status()
                payload: dict[str, Any] = response.json()
                return payload
            except httpx.HTTPStatusError as error:
                raise CollectionError(f"{url}: HTTP {error.response.status_code}") from error
            except (httpx.TransportError, ValueError) as error:
                last_error = error
                await asyncio.sleep(RETRY_BACKOFF_SECONDS * (attempt + 1))
        raise CollectionError(f"{url}: {TRANSIENT_RETRIES}회 재시도 후 실패 — {last_error}")
