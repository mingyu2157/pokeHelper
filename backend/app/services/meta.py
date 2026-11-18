"""메타 현황 유스케이스. 저장소 조회 + 표기 + 표시용 변환을 조합한다.

★ 이 서비스는 어느 사이트에서 데이터가 왔는지 모른다 ★

아는 것은 `snapshot.source` 라는 문자열뿐이고, 그걸 출처 표기 레지스트리에
넘긴다 (CLAUDE.md 8-6). 출처가 하나 더 붙어도 이 파일은 바뀌지 않는다.

★ 한글명이 없으면 없다고 말한다 ★

영문 id 를 한글명 자리에 넣지 않는다. 매핑 실패를 통과시키지 않는 원칙
(CLAUDE.md 8-4)의 표시 쪽 대응이다. 지금 도구 89종이 전부 미매핑인데
(Phase 2-3c), 영문 id 를 그냥 띄우면 사용자는 그게 **우리가 고른 표기**라고
읽는다. 그래서 `name_ko=None` 으로 내보내고 미매핑 개수를 함께 준다.
"""

from __future__ import annotations

from typing import Any, Final

from sqlalchemy.ext.asyncio import AsyncSession

from app.ingestion import attribution as attribution_registry
from app.repositories import meta as repo
from app.schemas.meta import (
    MAX_ENTRY_LIMIT,
    AttributionOut,
    MetaOptionOut,
    MetaSpreadOut,
    OptionCategory,
    PokemonMetaOut,
    SnapshotOut,
    UsageEntryOut,
    UsageListOut,
)

# UI 표준 HABCDS 표기 (CLAUDE.md 4장). 표기 순서가 곧 H-A-B-C-D-S 다.
# `services/ev_tune.py` 와 같은 순서를 쓴다 — 두 화면이 다른 순서로 쓰면
# 사용자는 같은 배분을 다른 것으로 읽는다.
_NOTATION_ORDER: Final[tuple[tuple[str, str], ...]] = (
    ("H", "hp"),
    ("A", "attack"),
    ("B", "defense"),
    ("C", "sp_attack"),
    ("D", "sp_defense"),
    ("S", "speed"),
)
_EMPTY_NOTATION: Final[str] = "-"

_CATEGORIES: Final[tuple[OptionCategory, ...]] = (
    "moves",
    "items",
    "abilities",
    "natures",
    "teammates",
)


class SnapshotNotFoundError(Exception):
    """해당 포맷의 활성 스냅샷이 없다.

    오류가 아니라 **상태**다. 수집을 한 번도 안 했거나, 검증 실패로 활성 전환이
    막혀 있다. 계산 기능은 이것과 무관하게 동작해야 한다 (CLAUDE.md 8-5).
    """


class PokemonNotInSnapshotError(Exception):
    """그 포켓몬이 활성 스냅샷에 없다. 순위권 밖이거나 id 가 틀렸다."""


def _snapshot_out(snapshot: repo.SnapshotInfo) -> SnapshotOut:
    return SnapshotOut(
        source=snapshot.source,
        format=snapshot.format,  # type: ignore[arg-type]
        regulation=snapshot.regulation_code,
        collected_at=snapshot.collected_at,
    )


def _attributions(snapshot: repo.SnapshotInfo) -> list[AttributionOut]:
    """이 응답에 실제로 들어간 데이터의 출처.

    목록인 이유: 한 화면이 두 출처를 섞을 수 있다. 지금 메타 화면은 채용률만
    보여주므로 하나지만, 기술 **위력·명중**을 같이 띄우는 순간 op.gg 표기가
    함께 나가야 한다 (ADR-009). 그때 구조를 바꾸지 않아도 되게 목록으로 둔다.
    """
    source = attribution_registry.for_source(snapshot.source)
    return [AttributionOut(text=source.text, url=source.url)]


def _notation(points: dict[str, int]) -> str:
    parts = [f"{letter}{points[field]}" for letter, field in _NOTATION_ORDER if points.get(field)]
    return "".join(parts) or _EMPTY_NOTATION


async def usage_list(session: AsyncSession, *, battle_format: str, limit: int) -> UsageListOut:
    """사용 순위표.

    `limit` 은 라우터가 스키마 상한으로 이미 막지만 여기서도 자른다 —
    서비스를 직접 부르는 다른 호출자(배치·테스트)가 상한을 우회하지 못하게 한다.
    """
    snapshot = await repo.active_snapshot(session, battle_format=battle_format)
    if snapshot is None:
        raise SnapshotNotFoundError(f"'{battle_format}' 포맷의 활성 스냅샷이 없다")

    entries = await repo.ranked_entries(
        session, snapshot_id=snapshot.id, limit=min(limit, MAX_ENTRY_LIMIT)
    )
    return UsageListOut(
        snapshot=_snapshot_out(snapshot),
        attributions=_attributions(snapshot),
        entries=[
            UsageEntryOut(
                pokemon_id=entry.pokemon_id,
                name_ko=entry.name_ko or None,
                rank=entry.rank,
                rank_change=_int_or_none(entry.data.get("rank_change")),
                usage_rate=entry.usage_rate,
            )
            for entry in entries
        ],
    )


async def pokemon_detail(
    session: AsyncSession, *, battle_format: str, pokemon_id: str
) -> PokemonMetaOut:
    """포켓몬 하나의 채용률 상세."""
    snapshot = await repo.active_snapshot(session, battle_format=battle_format)
    if snapshot is None:
        raise SnapshotNotFoundError(f"'{battle_format}' 포맷의 활성 스냅샷이 없다")

    entry = await repo.entry_for(session, snapshot_id=snapshot.id, pokemon_id=pokemon_id)
    if entry is None:
        raise PokemonNotInSnapshotError(f"'{pokemon_id}' 는 활성 스냅샷에 없다")

    option_rates = entry.data.get("option_rates") or {}
    options: dict[OptionCategory, list[MetaOptionOut]] = {}
    unmapped: dict[OptionCategory, int] = {}

    for category in _CATEGORIES:
        raw_options = _option_rows(option_rates.get(category))
        ids = [row["id"] for row in raw_options]
        names = await repo.korean_names(session, category=category, ids=ids)
        options[category] = [
            MetaOptionOut(
                option_id=row["id"],
                name_ko=names.get(row["id"]),
                rank=row["rank"],
                rate=row["rate"],
            )
            for row in raw_options
        ]
        unmapped[category] = sum(1 for row in raw_options if row["id"] not in names)

    return PokemonMetaOut(
        snapshot=_snapshot_out(snapshot),
        attributions=_attributions(snapshot),
        pokemon_id=entry.pokemon_id,
        name_ko=entry.name_ko or None,
        rank=entry.rank,
        rank_change=_int_or_none(entry.data.get("rank_change")),
        options=options,
        spreads=[
            MetaSpreadOut(
                notation=_notation(spread.get("points") or {}),
                points=spread.get("points") or {},
                rank=spread["rank"],
                rate=spread.get("rate"),
            )
            for spread in _spread_rows(entry.data.get("spreads"))
        ],
        unmapped=unmapped,
    )


def _option_rows(value: Any) -> list[dict[str, Any]]:
    """JSONB 에서 읽은 선택지 목록을 정규화한다.

    JSONB 는 스키마가 강제되지 않으므로 **모양을 믿지 않는다.** 수집기가 바뀌어
    키가 빠져도 화면 전체가 500 으로 죽는 대신 그 항목만 빠지는 쪽이 낫다.
    """
    if not isinstance(value, list):
        return []
    rows = [
        row
        for row in value
        if isinstance(row, dict) and isinstance(row.get("id"), str) and "rank" in row
    ]
    return sorted(rows, key=lambda row: row["rank"])


def _spread_rows(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    rows = [row for row in value if isinstance(row, dict) and "rank" in row]
    return sorted(rows, key=lambda row: row["rank"])


def _int_or_none(value: Any) -> int | None:
    return value if isinstance(value, int) else None
