"""ETL 통합 테스트 (Phase 2-5).

★ 여기서 지켜야 하는 건 "수집이 된다"가 아니라 "틀린 게 활성화되지 않는다" 다 ★

수집은 깨질 수 있다. 출처가 항목을 추가하거나, 이름이 바뀌거나, 매핑이 비어 있을 수
있다. 그때 **이전 스냅샷으로 계속 서비스하는 것**이 요구사항이고(CLAUDE.md 8-5),
이 테스트가 그걸 지킨다.

외부 API 를 부르지 않는다. 수집기는 인터페이스로 분리돼 있으므로 가짜 출처를 넣는다.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.db.models import MetaSnapshot, PokemonMeta, Regulation
from app.ingestion.base import CollectionResult, OptionUsage, PokemonUsage, SpreadUsage
from app.repositories import snapshots
from app.services.ingest import ingest

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


@pytest.fixture
async def regulation(session):
    regulation = Regulation(
        code=f"ETL-{datetime.now(UTC).timestamp()}",
        name="ETL 테스트",
        started_at=date(2026, 1, 1),
        rules={},
    )
    session.add(regulation)
    await session.flush()
    return regulation


def _entry(showdown_id: str = "garchomp", *, rank: int = 1, **overrides) -> PokemonUsage:
    base = {
        "showdown_id": showdown_id,
        "rank": rank,
        "usage_rate": None,
        "moves": (OptionUsage("earthquake", 1, 75.8),),
        "items": (OptionUsage("choicescarf", 1, 32.9),),
        "abilities": (OptionUsage("roughskin", 1, 99.3),),
        "natures": (OptionUsage("jolly", 1, 31.0),),
        "teammates": (OptionUsage("dragonite", 1, None),),
        "spreads": (
            SpreadUsage(
                {"hp": 2, "attack": 32, "defense": 0, "sp_attack": 0, "sp_defense": 0, "speed": 32},
                1,
                34.6,
            ),
        ),
        "raw": {"rows": []},
    }
    base.update(overrides)
    return PokemonUsage(**base)


def _result(*entries: PokemonUsage, offset_seconds: int = 0) -> CollectionResult:
    return CollectionResult(
        source="championsbattledata",
        format="Singles",
        season="Current",
        collected_at=datetime.now(UTC) + timedelta(seconds=offset_seconds),
        entries=entries or (_entry(),),
    )


class TestHappyPath:
    async def test_snapshot_is_activated(self, session, regulation):
        report = await ingest(session, result=_result(), regulation_id=regulation.id)
        assert report.activated is True
        assert report.collected == 1
        assert report.unresolved == {}

    async def test_entries_are_stored(self, session, regulation):
        report = await ingest(session, result=_result(), regulation_id=regulation.id)
        row = await session.scalar(
            select(PokemonMeta).where(PokemonMeta.snapshot_id == report.snapshot_id)
        )
        assert row is not None
        assert row.pokemon_id == "garchomp"
        assert row.rank == 1

    async def test_usage_rate_stays_null(self, session, regulation):
        """출처가 사용률 %를 주지 않는다. 0 으로 채우면 '아무도 안 쓴다'가 된다."""
        report = await ingest(session, result=_result(), regulation_id=regulation.id)
        row = await session.scalar(
            select(PokemonMeta).where(PokemonMeta.snapshot_id == report.snapshot_id)
        )
        assert row is not None and row.usage_rate is None

    async def test_raw_is_kept(self, session, regulation):
        """파싱을 고쳤을 때 재수집 없이 재파싱하려면 원본이 남아 있어야 한다."""
        report = await ingest(session, result=_result(), regulation_id=regulation.id)
        row = await session.scalar(
            select(PokemonMeta).where(PokemonMeta.snapshot_id == report.snapshot_id)
        )
        assert row is not None and row.raw is not None

    async def test_expiry_is_set(self, session, regulation):
        """보존 기한이 없으면 영구 아카이브가 된다 (CLAUDE.md 8-2)."""
        report = await ingest(session, result=_result(), regulation_id=regulation.id)
        snapshot = await session.get(MetaSnapshot, report.snapshot_id)
        assert snapshot is not None
        assert snapshot.expires_at > snapshot.collected_at


class TestValidationBlocksActivation:
    """★ 틀린 데이터가 활성화되지 않는다."""

    async def test_unknown_move_blocks_activation(self, session, regulation):
        bad = _entry(moves=(OptionUsage("supermegabeam", 1, 50.0),))
        report = await ingest(session, result=_result(bad), regulation_id=regulation.id)

        assert report.activated is False
        assert "supermegabeam" in report.unresolved["moves"]

    async def test_unknown_pokemon_blocks_activation(self, session, regulation):
        report = await ingest(
            session, result=_result(_entry("mysterymon")), regulation_id=regulation.id
        )
        assert report.activated is False
        assert "mysterymon" in report.unresolved["pokemon"]

    async def test_unknown_ability_blocks_activation(self, session, regulation):
        bad = _entry(abilities=(OptionUsage("ultrainstinct", 1, 100.0),))
        report = await ingest(session, result=_result(bad), regulation_id=regulation.id)
        assert report.activated is False
        assert "ultrainstinct" in report.unresolved["abilities"]

    async def test_data_is_still_stored_when_blocked(self, session, regulation):
        """적재는 하고 전환만 막는다 — 무엇이 왜 실패했는지 보려면 원본이 필요하다."""
        bad = _entry(moves=(OptionUsage("supermegabeam", 1, 50.0),))
        report = await ingest(session, result=_result(bad), regulation_id=regulation.id)

        snapshot = await session.get(MetaSnapshot, report.snapshot_id)
        assert snapshot is not None and snapshot.is_active is False
        assert report.collected == 1

    async def test_previous_snapshot_keeps_serving(self, session, regulation):
        """★ 요구사항 그대로: 갱신 실패 시 이전 스냅샷으로 계속 서비스한다."""
        good = await ingest(session, result=_result(), regulation_id=regulation.id)
        assert good.activated is True

        bad_entry = _entry(moves=(OptionUsage("supermegabeam", 1, 50.0),))
        bad = await ingest(
            session,
            result=_result(bad_entry, offset_seconds=60),
            regulation_id=regulation.id,
        )
        assert bad.activated is False

        active = await snapshots.get_active(
            session, regulation_id=regulation.id, battle_format="Singles"
        )
        assert active is not None
        assert active.id == good.snapshot_id, "이전 스냅샷이 활성으로 남아야 한다"


class TestSwitching:
    async def test_new_snapshot_replaces_the_old_one(self, session, regulation):
        first = await ingest(session, result=_result(), regulation_id=regulation.id)
        second = await ingest(
            session, result=_result(offset_seconds=60), regulation_id=regulation.id
        )

        assert second.activated is True
        active = await snapshots.get_active(
            session, regulation_id=regulation.id, battle_format="Singles"
        )
        assert active is not None and active.id == second.snapshot_id

        old = await session.get(MetaSnapshot, first.snapshot_id)
        assert old is not None and old.is_active is False

    async def test_only_one_active_at_a_time(self, session, regulation):
        await ingest(session, result=_result(), regulation_id=regulation.id)
        await ingest(session, result=_result(offset_seconds=60), regulation_id=regulation.id)
        await ingest(session, result=_result(offset_seconds=120), regulation_id=regulation.id)

        rows = await session.execute(
            select(MetaSnapshot).where(
                MetaSnapshot.regulation_id == regulation.id,
                MetaSnapshot.is_active.is_(True),
            )
        )
        assert len(rows.scalars().all()) == 1


class TestRankChangeIsDerived:
    """순위 변동을 우리 스냅샷에서 만든다 — 출처는 이걸 주지 않는다."""

    async def test_first_snapshot_has_no_change(self, session, regulation):
        report = await ingest(session, result=_result(), regulation_id=regulation.id)
        row = await session.scalar(
            select(PokemonMeta).where(PokemonMeta.snapshot_id == report.snapshot_id)
        )
        assert row is not None
        assert row.data["rank_change"] is None
        assert row.data["previous_rank"] is None

    async def test_rise_is_positive(self, session, regulation):
        await ingest(session, result=_result(_entry(rank=5)), regulation_id=regulation.id)
        second = await ingest(
            session,
            result=_result(_entry(rank=2), offset_seconds=60),
            regulation_id=regulation.id,
        )
        row = await session.scalar(
            select(PokemonMeta).where(PokemonMeta.snapshot_id == second.snapshot_id)
        )
        assert row is not None
        assert row.data["previous_rank"] == 5
        assert row.data["rank_change"] == 3, "5위 -> 2위 는 3계단 상승이다"

    async def test_fall_is_negative(self, session, regulation):
        await ingest(session, result=_result(_entry(rank=1)), regulation_id=regulation.id)
        second = await ingest(
            session,
            result=_result(_entry(rank=4), offset_seconds=60),
            regulation_id=regulation.id,
        )
        row = await session.scalar(
            select(PokemonMeta).where(PokemonMeta.snapshot_id == second.snapshot_id)
        )
        assert row is not None and row.data["rank_change"] == -3


class TestRetention:
    async def test_expired_inactive_snapshot_is_deleted(self, session, regulation):
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

    async def test_active_snapshot_survives_expiry(self, session, regulation):
        """★ 만료됐어도 활성이면 지우지 않는다 — 지우면 볼 메타가 사라진다.

        만료 상태를 만들 때 `expires_at` 을 과거로 밀지 않는다. 그러면
        `ck_snapshot_expiry_after_collection` 에 걸린다 (수집 시각보다 앞선 만료는
        애초에 불가능한 상태다). 대신 **수집 시각 자체를 과거로** 둔다.
        """
        snapshot = await snapshots.create_snapshot(
            session,
            regulation_id=regulation.id,
            source="championsbattledata",
            battle_format="Singles",
            collected_at=datetime.now(UTC) - timedelta(days=30),
            retention_days=14,  # 만료 = 16일 전
        )
        await snapshots.activate(session, snapshot)
        assert snapshot.is_active is True

        await snapshots.delete_expired(session, now=datetime.now(UTC))
        assert await session.get(MetaSnapshot, snapshot.id) is not None
