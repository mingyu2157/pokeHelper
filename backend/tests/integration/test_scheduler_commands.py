"""스케줄러가 호출할 커맨드의 통합 테스트 (Phase 2-6).

★ 여기서 지키는 것은 '스케줄러가 진실을 보는가' 다 ★

이 커맨드들은 사람이 보지 않는 시각에 돈다. 실패가 조용하면 며칠이 지나간다.
그래서 **종료 코드와 삭제 범위**를 테스트로 고정한다.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.db.models import MetaSnapshot, Regulation
from app.ingestion.championsbattledata import CollectionError, resolve_season
from app.repositories import snapshots
from scripts.run_ingest import EXIT_NOT_ACTIVATED, EXIT_OK

pytestmark = pytest.mark.db


@pytest.fixture
async def session():
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    try:
        async with factory() as session:
            yield session
            await session.rollback()
    finally:
        await engine.dispose()


class TestSeasonResolution:
    """★ 'Current' 를 레귤레이션 코드로 쓰면 시즌이 넘어갈 때 데이터가 섞인다."""

    def test_alias_becomes_concrete_season(self):
        assert resolve_season({"season": "Current", "seasons": ["Current", "M7", "M6"]}) == "M7"

    def test_concrete_season_passes_through(self):
        assert resolve_season({"season": "M6", "seasons": ["Current", "M7", "M6"]}) == "M6"

    def test_unresolvable_alias_fails(self):
        """추측하지 않는다. 별칭밖에 없으면 실패한다."""
        with pytest.raises(CollectionError):
            resolve_season({"season": "Current", "seasons": ["Current"]})

    def test_missing_seasons_fails(self):
        with pytest.raises(CollectionError):
            resolve_season({"season": "Current"})


class TestEnsureRegulation:
    async def test_creates_when_missing(self, session):
        code = f"T{datetime.now(UTC).timestamp()}"
        regulation = await snapshots.ensure_regulation(
            session, code=code, today=datetime.now(UTC).date()
        )
        assert regulation.id is not None
        assert regulation.rules["auto_created"] is True

    async def test_is_idempotent(self, session):
        code = f"T{datetime.now(UTC).timestamp()}"
        first = await snapshots.ensure_regulation(
            session, code=code, today=datetime.now(UTC).date()
        )
        second = await snapshots.ensure_regulation(
            session, code=code, today=datetime.now(UTC).date()
        )
        assert first.id == second.id

        rows = await session.execute(select(Regulation).where(Regulation.code == code))
        assert len(rows.scalars().all()) == 1

    async def test_does_not_overwrite_human_edits(self, session):
        """사람이 이름·기간을 채운 뒤에도 수집이 덮어쓰지 않아야 한다."""
        code = f"T{datetime.now(UTC).timestamp()}"
        created = await snapshots.ensure_regulation(
            session, code=code, today=datetime.now(UTC).date()
        )
        created.name = "시즌 7 (싱글)"
        created.rules = {"auto_created": False, "level": 50}
        await session.flush()

        again = await snapshots.ensure_regulation(
            session, code=code, today=datetime.now(UTC).date()
        )
        assert again.name == "시즌 7 (싱글)"
        assert again.rules["level"] == 50


class TestExitCodes:
    """★ 가장 위험한 경우는 '수집은 됐는데 활성화 못 함' 이다.

    데이터는 저장됐지만 서비스가 보는 메타는 그대로다. 이걸 0 으로 끝내면
    스케줄러 UI 가 초록으로 뜨고 아무도 모른다.
    """

    def test_not_activated_is_not_success(self):
        assert EXIT_NOT_ACTIVATED != EXIT_OK
        assert EXIT_NOT_ACTIVATED != 0


class TestCleanupScope:
    """정리 배치가 무엇을 지우고 무엇을 남기는가."""

    async def test_deletes_expired_inactive(self, session):
        code = f"T{datetime.now(UTC).timestamp()}"
        regulation = await snapshots.ensure_regulation(
            session, code=code, today=datetime.now(UTC).date()
        )
        snapshot = await snapshots.create_snapshot(
            session,
            regulation_id=regulation.id,
            source="championsbattledata",
            battle_format="Singles",
            collected_at=datetime.now(UTC) - timedelta(days=30),
            retention_days=14,
        )
        deleted = await snapshots.delete_expired(session, now=datetime.now(UTC))
        assert deleted >= 1
        assert await session.get(MetaSnapshot, snapshot.id) is None

    async def test_keeps_unexpired(self, session):
        code = f"T{datetime.now(UTC).timestamp()}"
        regulation = await snapshots.ensure_regulation(
            session, code=code, today=datetime.now(UTC).date()
        )
        fresh = await snapshots.create_snapshot(
            session,
            regulation_id=regulation.id,
            source="championsbattledata",
            battle_format="Singles",
            collected_at=datetime.now(UTC),
            retention_days=14,
        )
        await snapshots.delete_expired(session, now=datetime.now(UTC))
        assert await session.get(MetaSnapshot, fresh.id) is not None

    async def test_keeps_expired_but_active(self, session):
        """★ 만료돼도 활성이면 남긴다. 지우면 서비스가 볼 메타가 사라진다."""
        code = f"T{datetime.now(UTC).timestamp()}"
        regulation = await snapshots.ensure_regulation(
            session, code=code, today=datetime.now(UTC).date()
        )
        stale = await snapshots.create_snapshot(
            session,
            regulation_id=regulation.id,
            source="championsbattledata",
            battle_format="Singles",
            collected_at=datetime.now(UTC) - timedelta(days=30),
            retention_days=14,
        )
        await snapshots.activate(session, stale)

        await snapshots.delete_expired(session, now=datetime.now(UTC))
        assert await session.get(MetaSnapshot, stale.id) is not None
