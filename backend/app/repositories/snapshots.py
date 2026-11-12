"""스냅샷 저장소. DB 접근만 한다 (CLAUDE.md 3장).

★ 전환은 한 트랜잭션에서 일어난다 ★

이전 스냅샷을 끄고 새 것을 켜는 두 UPDATE 가 나뉘면, 그 사이에 활성 스냅샷이
0개이거나 2개인 순간이 생긴다. 0개면 서비스가 메타 없이 돌고, 2개면 어느 쪽을
보는지 알 수 없다. 부분 유니크 인덱스(`WHERE is_active`)가 2개인 상태를 DB 에서
막지만, 애초에 그 상태를 만들지 않는 게 맞다.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import MetaSnapshot, PokemonMeta

# 보존 기간. 가이드 4-3 의 권장값이고, 영구 미러 금지(CLAUDE.md 8-2)의 실제 장치다.
RETENTION_DAYS: int = 14


async def create_snapshot(
    session: AsyncSession,
    *,
    regulation_id: int,
    source: str,
    battle_format: str,
    collected_at: datetime,
    retention_days: int = RETENTION_DAYS,
) -> MetaSnapshot:
    """비활성 상태로 만든다. 검증을 통과해야 활성화된다."""
    snapshot = MetaSnapshot(
        regulation_id=regulation_id,
        source=source,
        format=battle_format,
        collected_at=collected_at,
        expires_at=collected_at + timedelta(days=retention_days),
        is_active=False,
    )
    session.add(snapshot)
    await session.flush()
    return snapshot


async def add_entries(
    session: AsyncSession, *, snapshot_id: int, entries: list[dict[str, Any]]
) -> int:
    """스냅샷에 포켓몬별 메타를 넣는다."""
    if not entries:
        return 0
    session.add_all([PokemonMeta(snapshot_id=snapshot_id, **entry) for entry in entries])
    await session.flush()
    return len(entries)


async def activate(session: AsyncSession, snapshot: MetaSnapshot) -> None:
    """이 스냅샷을 활성화하고 같은 (레귤레이션, 포맷)의 나머지를 끈다.

    끄기를 **먼저** 한다. 켜기를 먼저 하면 그 순간 활성이 둘이 되어
    부분 유니크 인덱스에 걸린다.
    """
    await session.execute(
        update(MetaSnapshot)
        .where(
            MetaSnapshot.regulation_id == snapshot.regulation_id,
            MetaSnapshot.format == snapshot.format,
            MetaSnapshot.id != snapshot.id,
            MetaSnapshot.is_active.is_(True),
        )
        .values(is_active=False)
    )
    snapshot.is_active = True
    await session.flush()


async def get_active(
    session: AsyncSession, *, regulation_id: int, battle_format: str
) -> MetaSnapshot | None:
    return await session.scalar(
        select(MetaSnapshot).where(
            MetaSnapshot.regulation_id == regulation_id,
            MetaSnapshot.format == battle_format,
            MetaSnapshot.is_active.is_(True),
        )
    )


async def previous_ranks(
    session: AsyncSession, *, regulation_id: int, battle_format: str, before_id: int
) -> dict[str, int]:
    """직전 스냅샷의 포켓몬별 순위.

    순위 변동(상승/하강)을 **우리 데이터에서 파생**하기 위한 것이다. 외부 출처가
    변동 정보를 주지 않아도, 스냅샷을 보존 기간만큼 들고 있으면 직접 계산할 수 있다.
    """
    previous = await session.scalar(
        select(MetaSnapshot)
        .where(
            MetaSnapshot.regulation_id == regulation_id,
            MetaSnapshot.format == battle_format,
            MetaSnapshot.id < before_id,
        )
        .order_by(MetaSnapshot.collected_at.desc())
        .limit(1)
    )
    if previous is None:
        return {}

    rows = await session.execute(
        select(PokemonMeta.pokemon_id, PokemonMeta.rank).where(
            PokemonMeta.snapshot_id == previous.id, PokemonMeta.rank.is_not(None)
        )
    )
    return {pokemon_id: rank for pokemon_id, rank in rows if rank is not None}


async def delete_expired(session: AsyncSession, *, now: datetime) -> int:
    """보존 기한이 지난 스냅샷을 지운다 (Phase 2-6 정리 배치).

    **활성 스냅샷은 만료돼도 지우지 않는다.** 지우면 서비스가 볼 메타가 사라진다.
    수집이 며칠 멈춰도 마지막 스냅샷으로 계속 서비스해야 한다 (CLAUDE.md 8-5).
    하위 행은 FK 의 ON DELETE CASCADE 가 같이 지운다.
    """
    result = await session.execute(
        delete(MetaSnapshot).where(MetaSnapshot.expires_at < now, MetaSnapshot.is_active.is_(False))
    )
    return result.rowcount or 0
