"""지표 노출 통합 테스트 (ADR-016).

**실제 Postgres 를 쓴다.** `/metrics` 가 스크랩 때 DB 를 한 번 읽기 때문이다.
가짜 DB 로 대체하면 "DB 를 못 읽었을 때 직전 값을 유지한다"는 핵심 동작을
검증할 수 없다.

`db` 마커가 붙어 있다.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.db.models import MetaSnapshot, Regulation
from app.main import LATENCY_BUCKETS, app
from app.repositories import snapshots

pytestmark = pytest.mark.db


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture(scope="module")
def exposition(client: TestClient) -> str:
    """지표 본문 한 번만 받아 재사용한다. 호출마다 DB 를 읽을 이유가 없다."""
    client.get("/health")
    response = client.get("/metrics")
    assert response.status_code == 200
    return response.text


@pytest.fixture
async def session():
    """테스트마다 롤백한다. 활성 스냅샷을 커밋하면 부분 유니크 인덱스
    (`uq_meta_snapshot_active`)가 개발 DB 의 실제 스냅샷과 충돌하고, 남으면
    서비스가 잘못된 메타를 본다."""
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    try:
        async with factory() as session:
            yield session
            await session.rollback()
    finally:
        await engine.dispose()


class TestExposition:
    """계약 검증 — 경보와 대시보드가 문자열로 참조하는 지표가 실제로 나오는가.

    PromQL 에서 존재하지 않는 지표는 오류가 아니라 **빈 결과**다. 이름이
    어긋나면 경보는 유효한 상태로 영원히 울리지 않는다. 그 어긋남을 여기서 잡는다.
    """

    def test_endpoint_is_served_in_prometheus_format(self, client: TestClient) -> None:
        response = client.get("/metrics")
        assert response.status_code == 200
        assert "text/plain" in response.headers["content-type"]

    @pytest.mark.parametrize(
        "metric",
        [
            "pokehelper_etl_active_snapshots",
            "pokehelper_etl_freshness_refresh_failures_total",
            "pokehelper_etl_freshness_last_refresh_timestamp_seconds",
            "http_requests_total",
            "http_request_duration_seconds_bucket",
        ],
    )
    def test_metric_is_exposed(self, exposition: str, metric: str) -> None:
        assert metric in exposition, f"{metric} 가 노출되지 않는다"

    def test_the_database_read_succeeded(self, exposition: str) -> None:
        """실패 카운터가 0 이고 마지막 갱신 시각이 찍혀 있어야 DB 를 읽은 것이다.

        게이지 존재만 보면 안 된다 — 실패해도 게이지는 직전 값으로 남아 있으므로
        "노출된다"와 "방금 읽었다"는 다른 사실이다.
        """
        assert "pokehelper_etl_freshness_refresh_failures_total 0.0" in exposition
        line = next(
            ln
            for ln in exposition.splitlines()
            if ln.startswith("pokehelper_etl_freshness_last_refresh_timestamp_seconds ")
        )
        assert float(line.split()[1]) > 0


class TestLatencyBuckets:
    """경계값 분석 — 느린 경로가 최상단(+Inf) 버킷에 몰리지 않는지.

    라이브러리 기본 경로별 버킷은 (0.1, 0.5, 1.0) 셋뿐이다. 노력치 솔버는
    정상 입력(제약 8개)에서 2.2초를 쓴다(2026-11-17 실측). 기본값이면 그 값이
    전부 +Inf 에 들어가서 **경로별 p95 를 계산할 수 없다** — 1.1초와 60초가
    같은 칸에 있다. 대시보드가 멀쩡해 보이면서 정작 느린 하나를 못 본다.
    """

    def test_buckets_bracket_the_measured_worst_case(self) -> None:
        """실측 최악값 2.2초가 유한 버킷 사이에 들어가야 한다."""
        measured_worst = 2.2
        assert min(LATENCY_BUCKETS) <= 0.01, "빠른 경로(2.6ms)의 해상도가 없다"
        assert max(LATENCY_BUCKETS) > measured_worst, "최악값이 +Inf 로 떨어진다"
        assert any(b > measured_worst for b in LATENCY_BUCKETS)

    def test_exposed_buckets_match_the_configured_ones(self, exposition: str) -> None:
        """설정한 버킷이 실제 노출과 일치한다. 선언과 노출은 다른 사실이다."""
        exposed = {
            float(part.split('"')[0])
            for line in exposition.splitlines()
            if line.startswith("http_request_duration_seconds_bucket")
            for part in [line.split('le="', 1)[1]]
            if not part.startswith("+Inf")
        }
        assert exposed == set(LATENCY_BUCKETS)


class TestSelfExclusion:
    """`/metrics` 는 자기 자신의 요청 지표에 들어가지 않는다.

    포함되면 스크랩 간격이 요청률 그래프에 상수로 깔려서, 실제 사용자 트래픽이
    그 아래 묻힌다. 15초 간격이면 분당 4건이 항상 깔린다.
    """

    def test_metrics_handler_is_not_counted(self, exposition: str) -> None:
        assert 'handler="/metrics"' not in exposition

    def test_other_handlers_are_counted(self, exposition: str) -> None:
        """제외가 과하게 걸려 전부 사라진 것이 아님을 같이 단정한다."""
        assert 'handler="/health"' in exposition


class TestActiveFreshnessQuery:
    """활성 스냅샷 조회 — 지표의 입력이 되는 SQL (롤백하므로 DB 를 더럽히지 않는다).

    `/metrics` 본문만 보면 활성 스냅샷이 없는 환경(CI)에서는 계열이 아예 없어
    아무것도 검증되지 않는다. 그래서 질의 자체를 따로 집는다.
    """

    async def test_groups_by_source_and_format_taking_the_latest(self, session) -> None:
        now = datetime.now(UTC)
        regulation = Regulation(
            code="OBS-TEST",
            name="관측 테스트",
            started_at=now.date(),
            rules={"level": 50},
        )
        session.add(regulation)
        await session.flush()

        # 같은 (출처, 포맷)에 두 건. 하나만 활성이고, 지표는 활성만 본다.
        session.add_all(
            [
                MetaSnapshot(
                    regulation_id=regulation.id,
                    source="obs-test",
                    format="Singles",
                    collected_at=now - timedelta(days=3),
                    expires_at=now + timedelta(days=11),
                    is_active=False,
                ),
                MetaSnapshot(
                    regulation_id=regulation.id,
                    source="obs-test",
                    format="Singles",
                    collected_at=now - timedelta(days=1),
                    expires_at=now + timedelta(days=13),
                    is_active=True,
                ),
            ]
        )
        await session.flush()

        rows = await snapshots.active_freshness(session)
        ours = [row for row in rows if row[0] == "obs-test"]

        assert len(ours) == 1, "출처·포맷으로 묶이지 않았다"
        source, battle_format, collected_at = ours[0]
        assert (source, battle_format) == ("obs-test", "Singles")
        # 비활성 쪽이 더 오래됐다. 활성만 봤다면 1일 전이 나온다.
        assert collected_at == now - timedelta(days=1)

    async def test_inactive_snapshots_are_ignored(self, session) -> None:
        """비활성 스냅샷이 더 최신이어도 지표에 들어가면 안 된다.

        수집이 실패해 활성 전환이 되지 않은 상황이 정확히 이것이고, 그게
        경보로 울려야 하는 상태다. 비활성을 포함하면 **실패가 성공으로 보인다.**
        """
        now = datetime.now(UTC)
        regulation = Regulation(
            code="OBS-TEST-2",
            name="관측 테스트 2",
            started_at=now.date(),
            rules={"level": 50},
        )
        session.add(regulation)
        await session.flush()

        session.add(
            MetaSnapshot(
                regulation_id=regulation.id,
                source="obs-test-2",
                format="Singles",
                collected_at=now,
                expires_at=now + timedelta(days=14),
                is_active=False,
            )
        )
        await session.flush()

        rows = await snapshots.active_freshness(session)
        assert [row for row in rows if row[0] == "obs-test-2"] == []
