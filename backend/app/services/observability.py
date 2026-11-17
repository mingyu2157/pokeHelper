"""관측 지표 — Prometheus 노출 (ADR-016).

★ 왜 ETL 지표를 우리 앱이 내보내는가 ★

배치 작업은 스크랩 대상이 될 수 없어서 보통 Pushgateway 를 얹는다. 이 프로젝트는
그렇게 하지 않는다. 이유가 둘이다.

1. **과정이 아니라 결과를 측정한다.** Airflow 가 "성공"을 보고해도 활성 스냅샷이
   갱신되지 않았으면 서비스 입장에서는 실패다. Pushgateway 방식은 그 상태를
   초록으로 본다. 우리가 알고 싶은 것은 "서비스가 바라보는 데이터가 얼마나
   오래됐는가" 이고, 그건 DAG 의 종료 코드가 아니라 DB 의 상태다.
2. postgres_exporter 의 커스텀 쿼리(`--extend.query-path`)는 v0.17.1 에서
   **DEPRECATED** 다 (`--help` 로 확인). 핵심 지표를 폐기 예정 플래그에 걸지 않는다.

★ 스크랩 때 읽는다. 백그라운드 루프가 아니다 ★

주기적으로 갱신하는 태스크를 두면 lifespan·태스크 취소·테스트에서의 유령 요청을
전부 다뤄야 한다. 스크랩 때 읽으면 호출 시점이 명확하고 테스트가 결정론적이다.
대신 두 장치를 둔다.

- **캐시**: 스크랩이 잦아도 DB 를 때리지 않는다 (기본 15초).
- **타임아웃**: DB 가 느리면 `/metrics` 가 같이 느려진다. 짧게 끊고 **직전 값을
  유지한다.** 0 으로 떨구지 않는다 — 타임스탬프 게이지에서 0 은 "1970년"이고,
  그건 "오래됐다"가 아니라 "무한히 오래됐다"로 읽혀 거짓 경보가 된다.

★ 타임스탬프 게이지만으로는 "한 번도 수집되지 않았다"를 표현할 수 없다 ★

`time() - last_success > 30h` 는 **계열이 존재할 때만** 성립한다. 활성 스냅샷이
0개면 계열 자체가 없어서 경보가 울리지 않는다. 그래서 개수 게이지를 따로 둔다.
"없음"과 "오래됨"은 다른 상태이고, 둘 다 알아야 한다.
"""

from __future__ import annotations

import asyncio
import logging
import time

from prometheus_client import Counter, Gauge
from sqlalchemy.exc import SQLAlchemyError

from app.core.config import get_settings
from app.db.session import get_session_factory
from app.repositories import snapshots

logger = logging.getLogger(__name__)

# ── 지표 정의 ────────────────────────────────────────────────────────────
#
# 이름은 Prometheus 관례를 따른다: `<namespace>_<subsystem>_<이름>_<단위>`.
# 타임스탬프 게이지는 `_timestamp_seconds`, 카운터는 `_total` 로 끝낸다.
# 경보 규칙이 `time() - metric` 을 쓰므로 **유닉스 초**여야 한다. 경과 시간을
# 직접 내보내면 스크랩 간격만큼 어긋나고, 앱이 멈춘 동안 값이 고정된다.

ETL_LAST_SUCCESS = Gauge(
    "pokehelper_etl_last_success_timestamp_seconds",
    "활성 스냅샷의 수집 시각 (유닉스 초). 수집 '성공'의 결과를 측정한다.",
    labelnames=("source", "format"),
)

ETL_ACTIVE_SNAPSHOTS = Gauge(
    "pokehelper_etl_active_snapshots",
    "활성 스냅샷 개수. 0 이면 서비스가 볼 메타가 없다 (= 한 번도 수집되지 않음).",
)

ETL_REFRESH_FAILURES = Counter(
    "pokehelper_etl_freshness_refresh_failures_total",
    "ETL 신선도 조회 실패 횟수. 늘어나면 지표 자체를 믿을 수 없다.",
)

ETL_LAST_REFRESH = Gauge(
    "pokehelper_etl_freshness_last_refresh_timestamp_seconds",
    "신선도 지표를 DB 에서 마지막으로 성공적으로 읽은 시각 (유닉스 초).",
)

# ── 캐시 ─────────────────────────────────────────────────────────────────
#
# 프로세스 전역 상태다. prometheus_client 의 레지스트리도 마찬가지이므로
# 새로 들여오는 성질은 아니다. 테스트가 제어할 수 있도록 reset 을 노출한다.

_cache_valid_until: float = 0.0


def reset_cache() -> None:
    """캐시를 무효화한다. 테스트가 갱신을 강제할 때 쓴다."""
    global _cache_valid_until
    _cache_valid_until = 0.0


async def _read_freshness() -> list[tuple[str, str, float]]:
    factory = get_session_factory()
    async with factory() as session:
        rows = await snapshots.active_freshness(session)
    return [(source, fmt, collected_at.timestamp()) for source, fmt, collected_at in rows]


async def refresh_etl_freshness() -> bool:
    """활성 스냅샷의 수집 시각을 게이지에 반영한다.

    성공하면 True. 실패하면 **게이지를 그대로 두고** False 를 돌려준다.
    실패를 0 으로 표현하면 경보가 거짓으로 울린다 (모듈 머리말 참조).
    """
    settings = get_settings()
    try:
        rows = await asyncio.wait_for(
            _read_freshness(), timeout=settings.metrics_db_timeout_seconds
        )
    # 맨 Exception 을 잡지 않는다 (CLAUDE.md 6장). 실제로 일어나는 둘만 잡는다 —
    # 타임아웃과 DB 오류다. 그 밖의 예외는 우리 버그이므로 숨기면 안 된다.
    except (TimeoutError, SQLAlchemyError) as exc:
        ETL_REFRESH_FAILURES.inc()
        logger.warning("ETL 신선도 조회 실패 — 직전 값을 유지한다: %s", exc)
        return False

    # 계열이 사라진 경우(스냅샷이 비활성화됨)를 반영하려면 먼저 비운다.
    # 비우지 않으면 없어진 (source, format) 조합이 영원히 옛 값으로 남는다.
    ETL_LAST_SUCCESS.clear()
    for source, fmt, timestamp in rows:
        ETL_LAST_SUCCESS.labels(source=source, format=fmt).set(timestamp)

    ETL_ACTIVE_SNAPSHOTS.set(len(rows))
    ETL_LAST_REFRESH.set(time.time())
    return True


async def refresh_if_stale(*, now: float | None = None) -> None:
    """캐시가 만료됐을 때만 DB 를 읽는다. 스크랩마다 때리지 않기 위한 장치다."""
    global _cache_valid_until
    settings = get_settings()
    current = time.monotonic() if now is None else now
    if current < _cache_valid_until:
        return
    # 실패해도 캐시를 밀어둔다. 실패하는 DB 에 스크랩마다 재시도하면
    # 장애 중에 부하를 더한다.
    _cache_valid_until = current + settings.metrics_cache_seconds
    await refresh_etl_freshness()
