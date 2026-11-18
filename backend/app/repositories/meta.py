"""메타 통계 읽기 저장소. DB 접근만 한다 (CLAUDE.md 3장).

`snapshots.py` 는 **쓰기**(수집·전환·정리) 쪽이고 여기는 **읽기** 쪽이다.
한 파일에 두면 수집 트랜잭션과 화면 조회가 섞여서, 조회를 고칠 때 전환 로직을
같이 읽게 된다.

★ 읽기는 언제나 활성 스냅샷 하나만 본다 ★

비활성 스냅샷이 더 최신이어도 보지 않는다. 검증에 실패해 활성 전환이 막힌
스냅샷이 정확히 그런 상태이고(수집 CLI 종료코드 2), 그걸 화면에 보여주면
**틀린 매핑이 섞인 데이터를 사용자에게 내보내는** 셈이 된다.

★ `raw` 는 여기서 절대 읽지 않는다 ★

`pokemon_meta.raw` 는 수집 원본이다. 그걸 API 로 흘리면 우리 서비스가
출처 데이터의 **덤프 엔드포인트**가 된다 — 라이선스가 금지하는 "경쟁 API·
대량 다운로드"다 (CLAUDE.md 8-2). 선택하지 않으면 실수로 나갈 일도 없다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    Ability,
    Item,
    MetaSnapshot,
    Move,
    Nature,
    Pokemon,
    PokemonMeta,
    Regulation,
)


@dataclass(frozen=True)
class SnapshotInfo:
    """표시에 필요한 스냅샷 정보. 내부 id 는 밖으로 내보내지 않는다."""

    id: int
    source: str
    format: str
    regulation_code: str
    collected_at: datetime


@dataclass(frozen=True)
class MetaEntry:
    """순위표 한 줄. 채용률 상세는 `data` 에 들어 있다."""

    pokemon_id: str
    name_ko: str | None
    rank: int | None
    usage_rate: float | None
    data: dict[str, Any]


async def active_snapshot(session: AsyncSession, *, battle_format: str) -> SnapshotInfo | None:
    """해당 포맷의 활성 스냅샷. 레귤레이션을 지정하지 않으면 가장 최근 것.

    활성 스냅샷은 (레귤레이션, 포맷)당 하나지만 레귤레이션이 여러 개면 포맷당
    여러 개가 될 수 있다. 화면은 "지금 쓰는 메타" 하나만 필요하므로
    **가장 최근 수집분**을 고른다.
    """
    row = (
        await session.execute(
            select(
                MetaSnapshot.id,
                MetaSnapshot.source,
                MetaSnapshot.format,
                Regulation.code,
                MetaSnapshot.collected_at,
            )
            .join(Regulation, Regulation.id == MetaSnapshot.regulation_id)
            .where(MetaSnapshot.format == battle_format, MetaSnapshot.is_active.is_(True))
            .order_by(MetaSnapshot.collected_at.desc())
            .limit(1)
        )
    ).first()
    if row is None:
        return None

    snapshot_id, source, fmt, regulation_code, collected_at = row
    return SnapshotInfo(
        id=snapshot_id,
        source=source,
        format=fmt,
        regulation_code=regulation_code,
        collected_at=collected_at,
    )


async def ranked_entries(session: AsyncSession, *, snapshot_id: int, limit: int) -> list[MetaEntry]:
    """순위순 엔트리. 순위가 없는 행은 뒤로 보낸다.

    `limit` 은 호출자가 **반드시** 준다. 기본값을 두면 "전부 주세요"가 기본이
    되고, 그 순간 이 엔드포인트가 덤프가 된다.
    """
    rows = await session.execute(
        select(
            PokemonMeta.pokemon_id,
            Pokemon.name_ko,
            PokemonMeta.rank,
            PokemonMeta.usage_rate,
            PokemonMeta.data,
        )
        .join(Pokemon, Pokemon.showdown_id == PokemonMeta.pokemon_id, isouter=True)
        .where(PokemonMeta.snapshot_id == snapshot_id)
        .order_by(PokemonMeta.rank.is_(None), PokemonMeta.rank, PokemonMeta.pokemon_id)
        .limit(limit)
    )
    return [
        MetaEntry(
            pokemon_id=pokemon_id,
            name_ko=name_ko,
            rank=rank,
            usage_rate=float(usage_rate) if usage_rate is not None else None,
            data=data or {},
        )
        for pokemon_id, name_ko, rank, usage_rate, data in rows
    ]


async def entry_for(
    session: AsyncSession, *, snapshot_id: int, pokemon_id: str
) -> MetaEntry | None:
    row = (
        await session.execute(
            select(
                PokemonMeta.pokemon_id,
                Pokemon.name_ko,
                PokemonMeta.rank,
                PokemonMeta.usage_rate,
                PokemonMeta.data,
            )
            .join(Pokemon, Pokemon.showdown_id == PokemonMeta.pokemon_id, isouter=True)
            .where(
                PokemonMeta.snapshot_id == snapshot_id,
                PokemonMeta.pokemon_id == pokemon_id,
            )
        )
    ).first()
    if row is None:
        return None
    pid, name_ko, rank, usage_rate, data = row
    return MetaEntry(
        pokemon_id=pid,
        name_ko=name_ko,
        rank=rank,
        usage_rate=float(usage_rate) if usage_rate is not None else None,
        data=data or {},
    )


async def korean_names(session: AsyncSession, *, category: str, ids: list[str]) -> dict[str, str]:
    """선택지 id → 한글명. **찾지 못한 id 는 결과에 넣지 않는다.**

    ★ 빈 문자열로 채우지 않는다 ★ `items.name_ko` 가 빈 문자열인 행이 실제로
    있는데(Phase 2-3c 미완), 그걸 "한글명 있음"으로 통과시키면 화면에 **빈 칸**이
    나온다. 빈 칸은 "이름이 없다"가 아니라 "뭔가 잘못됐다"로 읽힌다.
    호출자가 **미매핑임을 알고** 표시를 정하도록 아예 빼서 돌려준다
    (CLAUDE.md 8-4 fail-closed 의 표시 쪽 대응).
    """
    if not ids:
        return {}

    tables = {
        "moves": (Move.id, Move.name_ko),
        "items": (Item.id, Item.name_ko),
        "abilities": (Ability.id, Ability.name_ko),
        "natures": (Nature.id, Nature.name_ko),
        "teammates": (Pokemon.showdown_id, Pokemon.name_ko),
    }
    if category not in tables:
        return {}

    id_column, name_column = tables[category]
    rows = await session.execute(
        select(id_column, name_column).where(id_column.in_(ids), name_column.is_not(None))
    )
    return {key: name for key, name in rows if name}
