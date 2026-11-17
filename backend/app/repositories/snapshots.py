"""스냅샷 저장소. DB 접근만 한다 (CLAUDE.md 3장).

★ 전환은 한 트랜잭션에서 일어난다 ★

이전 스냅샷을 끄고 새 것을 켜는 두 UPDATE 가 나뉘면, 그 사이에 활성 스냅샷이
0개이거나 2개인 순간이 생긴다. 0개면 서비스가 메타 없이 돌고, 2개면 어느 쪽을
보는지 알 수 없다. 부분 유니크 인덱스(`WHERE is_active`)가 2개인 상태를 DB 에서
막지만, 애초에 그 상태를 만들지 않는 게 맞다.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import MetaSnapshot, PokemonMeta, Regulation

# 보존 기간. 가이드 4-3 의 권장값이고, 영구 미러 금지(CLAUDE.md 8-2)의 실제 장치다.
RETENTION_DAYS: int = 14


async def ensure_regulation(session: AsyncSession, *, code: str, today: date) -> Regulation:
    """레귤레이션을 코드로 찾고, 없으면 만든다.

    수집은 레귤레이션 스코프 안에서만 의미가 있는데, 그 정보를 우리에게 알려주는
    출처가 없다. 그래서 **수집한 시즌 코드로 자동 생성**하고 자동 생성임을 기록한다.
    사람이 나중에 이름·기간·규정을 채울 수 있다.

    코드는 구체 시즌("M7")이어야 한다. "Current" 같은 별칭을 넣으면 시즌이 넘어갈 때
    같은 행에 다른 시즌 데이터가 쌓인다.
    """
    existing = await session.scalar(select(Regulation).where(Regulation.code == code))
    if existing is not None:
        return existing

    regulation = Regulation(
        code=code,
        name=code,
        started_at=today,
        rules={
            "auto_created": True,
            "note": "수집 시 시즌 코드로 자동 생성했다. 이름·기간·규정은 사람이 채운다.",
        },
    )
    session.add(regulation)
    await session.flush()
    return regulation


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


async def active_freshness(session: AsyncSession) -> list[tuple[str, str, datetime]]:
    """활성 스냅샷의 (출처, 포맷, 가장 최근 수집 시각). 관측 지표용 (ADR-016).

    ★ 활성 스냅샷만 본다 ★ 비활성 스냅샷이 더 최신이어도 서비스는 그걸 보지
    않으므로, "서비스가 바라보는 데이터가 얼마나 오래됐는가" 와 무관하다.
    수집이 실패해 활성 전환이 안 된 경우가 정확히 그 상황이고, 그게 경보로
    울려야 하는 상태다.

    활성 스냅샷은 (레귤레이션, 포맷)당 하나이므로 레귤레이션이 여러 개면
    같은 포맷에 여러 행이 나온다. 지표는 레귤레이션을 구분하지 않으니
    (출처, 포맷)으로 묶어 **가장 최근 것**을 쓴다 — 라벨 조합을 레귤레이션까지
    늘리면 시즌이 바뀔 때마다 계열이 영구히 쌓인다.
    """
    rows = await session.execute(
        select(
            MetaSnapshot.source,
            MetaSnapshot.format,
            func.max(MetaSnapshot.collected_at),
        )
        .where(MetaSnapshot.is_active.is_(True))
        .group_by(MetaSnapshot.source, MetaSnapshot.format)
        .order_by(MetaSnapshot.source, MetaSnapshot.format)
    )
    return [(source, fmt, collected_at) for source, fmt, collected_at in rows]
