"""수집 ETL (Phase 2-5).

    Extract   StatsSource 에서 가져온다 (어느 사이트인지 모른다)
    Transform 내부 스키마로 정규화한다 (수집기가 이미 했다)
    Validate  ★ 모든 이름이 우리 마스터로 해석되는지 본다
    Load      비활성 스냅샷으로 적재한다
    Switch    검증을 통과했을 때만 활성 전환한다

★ 검증에 실패하면 전환하지 않는다 ★

ADR-005 의 fail-closed 다. 이전 스냅샷으로 계속 서비스하는 게, 틀린 이름이 섞인 새
스냅샷을 쓰는 것보다 낫다. 영문명을 그대로 통과시키거나 해석 안 되는 항목만
건너뛰지 않는다 — 건너뛰면 "이 기술 채용률 0%" 라는 틀린 화면이 나온다.

적재는 **하고** 전환만 막는다. 원본이 DB 에 남아 있어야 무엇이 왜 실패했는지 보고,
매핑을 고친 뒤 재수집 없이 다시 검증할 수 있다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.seed import ability_name_map, move_dex, nature_name_map, pokedex
from app.domain.names import NameMap
from app.ingestion.base import CollectionResult, PokemonUsage
from app.repositories import snapshots

# 도구는 아직 한영 매핑이 없다 (Phase 2-3c). 그래서 **해석 실패로 치지 않는다** —
# 지금 포함하면 모든 수집이 전환에 실패한다. 매핑이 생기면 이 집합에서 뺀다.
UNMAPPED_CATEGORIES: frozenset[str] = frozenset({"items"})


@dataclass
class IngestReport:
    """수집 결과. 전환 여부와 그 이유를 담는다."""

    snapshot_id: int
    collected: int
    activated: bool
    unresolved: dict[str, list[str]] = field(default_factory=dict)
    skipped_categories: tuple[str, ...] = ()

    @property
    def unresolved_count(self) -> int:
        return sum(len(values) for values in self.unresolved.values())

    def summary(self) -> str:
        if self.activated:
            return f"스냅샷 {self.snapshot_id} 활성화 (포켓몬 {self.collected}종)"
        return (
            f"스냅샷 {self.snapshot_id} 적재했으나 **활성화하지 않음** — "
            f"해석 실패 {self.unresolved_count}건. 이전 스냅샷으로 계속 서비스한다."
        )


def _resolve(
    entries: tuple[PokemonUsage, ...],
    *,
    known_pokemon: set[str],
    known_moves: set[str],
    abilities: NameMap,
    natures: NameMap,
) -> dict[str, list[str]]:
    """모든 id 가 마스터로 해석되는지 본다. 해석 안 되는 것만 모아 돌려준다."""
    unresolved: dict[str, set[str]] = {}

    def note(category: str, value: str) -> None:
        unresolved.setdefault(category, set()).add(value)

    for entry in entries:
        if entry.showdown_id not in known_pokemon:
            note("pokemon", entry.showdown_id)

        for option in entry.moves:
            if option.option_id not in known_moves:
                note("moves", option.option_id)
        for option in entry.abilities:
            if not abilities.has(option.option_id):
                note("abilities", option.option_id)
        for option in entry.natures:
            if not natures.has(option.option_id):
                note("natures", option.option_id)
        for option in entry.teammates:
            if option.option_id not in known_pokemon:
                note("teammates", option.option_id)

    return {category: sorted(values) for category, values in sorted(unresolved.items())}


def _entry_payload(entry: PokemonUsage, *, previous_rank: int | None) -> dict[str, Any]:
    """`pokemon_meta` 한 행. 채용률 상세는 JSONB 에 담는다.

    `rank_change` 는 **우리 스냅샷에서 파생**한다. 출처가 순위 변동을 주지 않지만,
    보존 기간 안의 직전 스냅샷과 비교하면 직접 계산할 수 있다.
    양수 = 순위 상승(숫자가 작아짐).
    """
    rank_change = (
        previous_rank - entry.rank if previous_rank is not None and entry.rank is not None else None
    )
    return {
        "pokemon_id": entry.showdown_id,
        "rank": entry.rank,
        # 출처가 포켓몬 사용률 %를 주지 않는다. 0 으로 채우지 않는다.
        "usage_rate": entry.usage_rate,
        "data": {
            "rank_change": rank_change,
            "previous_rank": previous_rank,
            "option_rates": {
                "moves": [_option(o) for o in entry.moves],
                "items": [_option(o) for o in entry.items],
                "abilities": [_option(o) for o in entry.abilities],
                "natures": [_option(o) for o in entry.natures],
                "teammates": [_option(o) for o in entry.teammates],
            },
            "spreads": [
                {"points": s.points, "rank": s.rank, "rate": s.rate} for s in entry.spreads
            ],
        },
        "raw": entry.raw,
    }


def _option(option: Any) -> dict[str, Any]:
    return {"id": option.option_id, "rank": option.rank, "rate": option.rate}


async def ingest(
    session: AsyncSession,
    *,
    result: CollectionResult,
    regulation_id: int,
    now: datetime | None = None,
) -> IngestReport:
    """수집 결과를 스냅샷으로 적재하고, 검증을 통과하면 활성화한다."""
    known_pokemon = set(pokedex().ids())
    known_moves = set(move_dex().ids())

    unresolved = _resolve(
        result.entries,
        known_pokemon=known_pokemon,
        known_moves=known_moves,
        abilities=ability_name_map(),
        natures=nature_name_map(),
    )

    snapshot = await snapshots.create_snapshot(
        session,
        regulation_id=regulation_id,
        source=result.source,
        battle_format=result.format,
        collected_at=result.collected_at,
    )

    previous = await snapshots.previous_ranks(
        session,
        regulation_id=regulation_id,
        battle_format=result.format,
        before_id=snapshot.id,
    )

    payloads = [
        _entry_payload(entry, previous_rank=previous.get(entry.showdown_id))
        for entry in result.entries
        if entry.showdown_id in known_pokemon
    ]
    await snapshots.add_entries(session, snapshot_id=snapshot.id, entries=payloads)

    report = IngestReport(
        snapshot_id=snapshot.id,
        collected=len(payloads),
        activated=False,
        unresolved=unresolved,
        skipped_categories=tuple(sorted(UNMAPPED_CATEGORIES)),
    )

    if not unresolved:
        await snapshots.activate(session, snapshot)
        report.activated = True

    return report
