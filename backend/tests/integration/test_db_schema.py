"""DB 스키마 통합 테스트 (Phase 2-4).

**실제 Postgres 를 쓴다.** SQLite 로 대체하지 않는다 — 이 스키마의 핵심 장치가
부분 유니크 인덱스(`WHERE is_active`)와 CHECK 제약인데, 둘 다 방언에 따라 동작이
다르거나 아예 없다. 가짜 DB 에서 통과하는 테스트는 이 스키마를 지켜주지 못한다.

`db` 마커가 붙어 있다. Postgres 컨테이너가 떠 있어야 돈다.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.db.models import (
    MetaSnapshot,
    Move,
    Nature,
    Pokemon,
    PokemonLearnset,
    PokemonMeta,
    Regulation,
)

pytestmark = pytest.mark.db


@pytest.fixture
async def session():
    """테스트마다 롤백한다. 적재된 시드를 더럽히지 않는다.

    엔진을 테스트마다 새로 만들고 `NullPool` 을 쓴다. 운영 코드의 엔진은
    프로세스당 하나로 캐시되는데, pytest-asyncio 는 테스트마다 이벤트 루프를
    새로 만든다. 커넥션이 닫힌 루프에 묶여 있으면 "Event loop is closed" 가 난다.
    """
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
        code=f"TEST-{datetime.now(UTC).timestamp()}",
        name="테스트 레귤레이션",
        started_at=date(2026, 1, 1),
        rules={},
    )
    session.add(regulation)
    await session.flush()
    return regulation


def _snapshot(regulation_id: int, *, active: bool, offset_seconds: int = 0) -> MetaSnapshot:
    collected = datetime.now(UTC) + timedelta(seconds=offset_seconds)
    return MetaSnapshot(
        regulation_id=regulation_id,
        source="championsbattledata",
        format="Singles",
        collected_at=collected,
        expires_at=collected + timedelta(days=14),
        is_active=active,
    )


class TestSeedIsLoaded:
    """시드 적재 결과가 DB 에 있는지. 없으면 ETL 이 참조할 마스터가 없다."""

    async def test_pokemon_master_is_populated(self, session):
        count = await session.scalar(select(text("count(*)")).select_from(Pokemon.__table__))
        assert count and count > 200

    async def test_korean_names_are_present(self, session):
        garchomp = await session.get(Pokemon, "garchomp")
        assert garchomp is not None
        assert garchomp.name_ko == "한카리아스"

    async def test_form_names_survived_the_round_trip(self, session):
        """괄호가 들어간 폼 표기가 그대로 저장돼야 한다."""
        raichu = await session.get(Pokemon, "raichualola")
        assert raichu is not None
        assert raichu.name_ko == "라이츄(알로라의 모습)"

    async def test_move_values_are_champions_values(self, session):
        """본가 데이터로 덮이면 여기서 깨진다 (ADR-009)."""
        beak_blast = await session.get(Move, "beakblast")
        assert beak_blast is not None
        assert beak_blast.power == 120
        assert beak_blast.pp == 8

    async def test_variable_power_move_keeps_null(self, session):
        """자이로볼의 위력은 0 이 아니라 '없음'이다."""
        gyro_ball = await session.get(Move, "gyroball")
        assert gyro_ball is not None
        assert gyro_ball.power is None

    async def test_nature_pairs_are_intact(self, session):
        jolly = await session.get(Nature, "jolly")
        assert jolly is not None
        assert (jolly.raised, jolly.lowered) == ("SPEED", "SP_ATTACK")
        assert jolly.name_ko == "명랑"


class TestLearnset:
    """Phase 2-4 에서 '데이터 없음'을 이유로 뺐다가 되살린 테이블이다."""

    async def test_pairs_are_loaded(self, session):
        count = await session.scalar(
            select(text("count(*)")).select_from(PokemonLearnset.__table__)
        )
        assert count and count > 10000

    async def test_rotom_learns_the_recovered_move(self, session):
        """★ 전기쇼크는 op.gg 목록 515개에 없던 기술이다.

        learnset 이 기술표의 누락을 잡아낸 증거를 DB 레벨에서도 고정한다.
        """
        count = await session.scalar(
            select(text("count(*)"))
            .select_from(PokemonLearnset.__table__)
            .where(PokemonLearnset.move_id == "thundershock")
        )
        assert count == 5

    async def test_unknown_move_is_rejected(self, session):
        """마스터에 없는 기술로 연결이 들어오면 FK 가 막는다 (fail-closed)."""
        session.add(PokemonLearnset(pokemon_id="garchomp", move_id="supermegabeam"))
        with pytest.raises((IntegrityError, DBAPIError)):
            await session.flush()

    async def test_unknown_pokemon_is_rejected(self, session):
        session.add(PokemonLearnset(pokemon_id="mysterymon", move_id="earthquake"))
        with pytest.raises((IntegrityError, DBAPIError)):
            await session.flush()


class TestConstraintsActuallyFire:
    """★ 제약이 선언만 돼 있고 동작하지 않으면 없는 것과 같다."""

    async def test_status_move_cannot_have_power(self, session):
        session.add(
            Move(
                id="testbadstatus",
                slug="test-bad-status",
                name_ko="테스트",
                type="normal",
                category="status",
                power=100,
                accuracy=None,
                pp=20,
                priority=0,
                contact=False,
                flags=[],
                target="selected",
            )
        )
        with pytest.raises((IntegrityError, DBAPIError)):
            await session.flush()

    async def test_nature_modifier_must_be_paired(self, session):
        """하나만 올리고 내리지 않는 성격은 존재하지 않는다 (CLAUDE.md 5-1)."""
        session.add(Nature(id="testhalf", name_ko="반쪽", raised="ATTACK", lowered=None))
        with pytest.raises((IntegrityError, DBAPIError)):
            await session.flush()

    async def test_pokemon_types_must_differ(self, session):
        session.add(
            Pokemon(
                showdown_id="testdual",
                name_en="Test",
                name_ko="테스트",
                display_name="Test",
                form_kind="Base",
                type1="fire",
                type2="fire",
                base_hp=50,
                base_atk=50,
                base_def=50,
                base_spa=50,
                base_spd=50,
                base_spe=50,
                ability_ids=[],
            )
        )
        with pytest.raises((IntegrityError, DBAPIError)):
            await session.flush()

    async def test_snapshot_must_expire_after_collection(self, session, regulation):
        """보존 기한이 수집 시각보다 앞서면 정리 배치가 바로 지운다."""
        now = datetime.now(UTC)
        session.add(
            MetaSnapshot(
                regulation_id=regulation.id,
                source="championsbattledata",
                format="Singles",
                collected_at=now,
                expires_at=now - timedelta(days=1),
                is_active=False,
            )
        )
        with pytest.raises((IntegrityError, DBAPIError)):
            await session.flush()


class TestOnlyOneActiveSnapshot:
    """★ 이 스키마에서 가장 중요한 보장.

    활성 스냅샷이 둘이면 "지금 서비스가 어느 데이터를 보고 있는가"를 알 수 없다.
    애플리케이션 코드로 막으면 전환 도중에 둘 다 활성인 순간이 생긴다.
    부분 유니크 인덱스로 **DB 가** 막는다.
    """

    async def test_second_active_snapshot_is_rejected(self, session, regulation):
        session.add(_snapshot(regulation.id, active=True))
        await session.flush()

        session.add(_snapshot(regulation.id, active=True, offset_seconds=60))
        with pytest.raises((IntegrityError, DBAPIError)):
            await session.flush()

    async def test_many_inactive_snapshots_are_fine(self, session, regulation):
        """비활성은 몇 개든 공존한다 — 롤백 대상으로 남겨둬야 한다."""
        for offset in range(3):
            session.add(_snapshot(regulation.id, active=False, offset_seconds=offset))
        await session.flush()

    async def test_different_formats_can_each_be_active(self, session, regulation):
        """싱글과 더블은 서로 독립이다."""
        singles = _snapshot(regulation.id, active=True)
        doubles = _snapshot(regulation.id, active=True, offset_seconds=1)
        doubles.format = "Doubles"
        session.add_all([singles, doubles])
        await session.flush()


class TestCascades:
    async def test_deleting_a_snapshot_removes_its_entries(self, session, regulation):
        """보존 기한 정리가 스냅샷만 지워도 하위 행이 남으면 안 된다 (Phase 2-6)."""
        snapshot = _snapshot(regulation.id, active=False)
        session.add(snapshot)
        await session.flush()

        session.add(
            PokemonMeta(
                snapshot_id=snapshot.id,
                pokemon_id="garchomp",
                rank=1,
                usage_rate=12.345,
                data={"option_rates": {}},
            )
        )
        await session.flush()

        await session.delete(snapshot)
        await session.flush()

        remaining = await session.scalar(
            select(text("count(*)"))
            .select_from(PokemonMeta.__table__)
            .where(PokemonMeta.snapshot_id == snapshot.id)
        )
        assert remaining == 0

    async def test_meta_cannot_reference_unknown_pokemon(self, session, regulation):
        """마스터에 없는 포켓몬으로 수집 행이 들어오면 막는다 (fail-closed)."""
        snapshot = _snapshot(regulation.id, active=False)
        session.add(snapshot)
        await session.flush()

        session.add(PokemonMeta(snapshot_id=snapshot.id, pokemon_id="mystery", rank=1, data={}))
        with pytest.raises((IntegrityError, DBAPIError)):
            await session.flush()
