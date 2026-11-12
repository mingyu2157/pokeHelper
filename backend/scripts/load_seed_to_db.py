"""JSON 시드를 DB 마스터 테이블에 적재한다 (Phase 2-4).

실행:
    docker compose exec backend uv run python -m scripts.load_seed_to_db

★ JSON 이 원천이고 DB 는 파생이다 (ADR-010) ★

이 스크립트는 한 방향으로만 흐른다: `data/*.json` -> DB. 반대 방향은 없다.
DB 를 손으로 고치면 다음 적재에서 덮어쓰인다. 그게 의도다 — 진실의 원천이
두 곳이 되는 순간 "어느 쪽이 맞나"를 영원히 묻게 된다.

**멱등하다.** 몇 번을 돌려도 결과가 같다. 기존 행은 갱신하고 없는 행은 넣는다.
시드에서 사라진 항목은 **지우지 않는다** — `pokemon_meta` 가 FK 로 참조하고 있을 수
있어서, 조용히 지우면 수집 데이터가 같이 날아간다. 사라진 항목은 보고만 한다.

★ 한글명이 없으면 적재하지 않는다 ★

`pokemon.name_ko` 는 NOT NULL 이다. 매핑이 비어 있으면 영문명을 넣어 때우지 않고
실패시킨다 (ADR-005 fail-closed). 도구만 예외로 `name_ko` 가 nullable 인데,
한영 매핑이 아직 없기 때문이다 (Phase 2-3c). 그때까지는 null 로 둔다 —
영문명을 넣으면 "한글명이 있다"고 거짓말하는 행이 생긴다.
"""

from __future__ import annotations

import asyncio
import json
import re
import sys
from pathlib import Path
from typing import Any, Final

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Ability, Item, Move, Nature, Pokemon
from app.db.session import get_engine, get_session_factory

DATA_DIR: Final[Path] = Path(__file__).resolve().parent.parent / "data"


def load(filename: str) -> dict[str, Any]:
    with (DATA_DIR / filename).open(encoding="utf-8") as file:
        data: dict[str, Any] = json.load(file)
    return data


def to_id(text: str) -> str:
    """showdown 식 id. 영숫자만 남긴 소문자."""
    return re.sub(r"[^a-z0-9]", "", text.lower())


async def upsert(session: AsyncSession, model: type[Any], rows: list[dict[str, Any]]) -> None:
    """기본키 충돌 시 갱신한다. 한 번에 보내서 왕복을 줄인다."""
    if not rows:
        return
    primary_key = next(iter(model.__table__.primary_key.columns)).name
    statement = insert(model.__table__).values(rows)
    updatable = {
        column.name: statement.excluded[column.name]
        for column in model.__table__.columns
        if column.name != primary_key
    }
    await session.execute(
        statement.on_conflict_do_update(index_elements=[primary_key], set_=updatable)
    )


def build_pokemon_rows() -> list[dict[str, Any]]:
    pokemon = load("pokemon.json")["pokemon"]
    names = load("name_map.json")["pokemon"]

    missing = sorted(key for key in pokemon if key not in names)
    if missing:
        raise SystemExit(
            f"한글명이 없는 포켓몬 {len(missing)}개: {missing[:10]}. "
            "영문명으로 때우지 않는다 — name_map 을 먼저 채운다 (ADR-005)."
        )

    rows: list[dict[str, Any]] = []
    for showdown_id, record in sorted(pokemon.items()):
        types = list(record["types"])
        stats = record["base_stats"]
        rows.append(
            {
                "showdown_id": showdown_id,
                "name_en": record["name"],
                "name_ko": names[showdown_id]["ko"],
                "display_name": record["display_name"],
                "form_kind": record["form_kind"],
                "type1": types[0],
                "type2": types[1] if len(types) > 1 else None,
                "base_hp": stats["hp"],
                "base_atk": stats["attack"],
                "base_def": stats["defense"],
                "base_spa": stats["sp_attack"],
                "base_spd": stats["sp_defense"],
                "base_spe": stats["speed"],
                "ability_ids": [to_id(name) for name in record["abilities"]],
            }
        )
    return rows


def build_move_rows() -> list[dict[str, Any]]:
    moves = load("moves.json")["moves"]
    names = load("name_map_moves.json")["moves"]

    missing = sorted(key for key in moves if key not in names)
    if missing:
        raise SystemExit(f"한글명이 없는 기술 {len(missing)}개: {missing[:10]}")

    return [
        {
            "id": move_id,
            "slug": record["slug"],
            "name_ko": names[move_id]["ko"],
            "type": record["type"],
            "category": record["category"],
            "power": record["power"],
            "accuracy": record["accuracy"],
            "pp": record["pp"],
            "priority": record["priority"],
            "contact": record["contact"],
            "flags": record["flags"],
            "target": record["target"],
        }
        for move_id, record in sorted(moves.items())
    ]


def build_ability_rows() -> list[dict[str, Any]]:
    abilities = load("name_map_abilities.json")["abilities"]
    return [
        {"id": ability_id, "name_en": record["en"], "name_ko": record["ko"]}
        for ability_id, record in sorted(abilities.items())
    ]


def build_nature_rows() -> list[dict[str, Any]]:
    natures = load("natures.json")["natures"]
    names = load("name_map_natures.json")["natures"]

    missing = sorted(key for key in natures if key not in names)
    if missing:
        raise SystemExit(f"한글명이 없는 성격: {missing}")

    return [
        {
            "id": nature_id,
            "name_ko": names[nature_id]["ko"],
            "raised": record["raised"],
            "lowered": record["lowered"],
        }
        for nature_id, record in sorted(natures.items())
    ]


def build_item_rows() -> list[dict[str, Any]]:
    """도구는 한글명 없이 적재한다. 매핑이 생기면 name_ko 를 채운다 (Phase 2-3c)."""
    items = load("items.json")["items"]
    return [
        {
            "id": item_id,
            "name_en": record["name_en"],
            "name_ko": None,
            "kind": record["kind"],
            "multiplier": record.get("multiplier"),
            "source": record["source"],
        }
        for item_id, record in sorted(items.items())
    ]


async def report_orphans(session: AsyncSession, model: type[Any], seed_ids: set[str]) -> list[str]:
    """시드에서 사라졌는데 DB 에 남아 있는 행. 지우지 않고 보고만 한다."""
    primary_key = next(iter(model.__table__.primary_key.columns))
    existing = set((await session.execute(select(primary_key))).scalars())
    return sorted(existing - seed_ids)


async def main() -> None:
    builders = (
        ("포켓몬", Pokemon, build_pokemon_rows(), "showdown_id"),
        ("기술", Move, build_move_rows(), "id"),
        ("특성", Ability, build_ability_rows(), "id"),
        ("성격", Nature, build_nature_rows(), "id"),
        ("도구", Item, build_item_rows(), "id"),
    )

    factory = get_session_factory()
    orphan_report: list[str] = []
    async with factory() as session:
        for label, model, rows, key in builders:
            await upsert(session, model, rows)
            orphans = await report_orphans(session, model, {row[key] for row in rows})
            if orphans:
                orphan_report.append(f"{label}: 시드에 없는데 DB 에 남은 행 {orphans}")
            print(f"  {label} {len(rows)}건 적재")
        await session.commit()

    if orphan_report:
        print("\n확인 필요 (지우지 않았다 — 수집 데이터가 참조 중일 수 있다):", file=sys.stderr)
        for line in orphan_report:
            print(f"  {line}", file=sys.stderr)

    await get_engine().dispose()
    print("\n적재 완료.")


if __name__ == "__main__":
    asyncio.run(main())
