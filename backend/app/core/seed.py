"""시드 데이터 로더.

`backend/data/*.json` 을 읽어 도메인 객체로 만든다.
**도메인은 파일을 읽지 않는다** (ADR-007) — 그 책임이 여기 있다.

프로세스당 한 번만 읽고 재사용한다. 레귤레이션 단위로 바뀌는 정적 데이터라
런타임에 다시 읽을 이유가 없다.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Final

from app.domain.items import ItemCatalog
from app.domain.learnset import Learnset
from app.domain.movedex import MoveDex
from app.domain.names import NameMap
from app.domain.nature import NatureCatalog
from app.domain.pokedex import Pokedex
from app.domain.typechart import TypeChart

DATA_DIR: Final[Path] = Path(__file__).resolve().parent.parent.parent / "data"


def _load(filename: str) -> dict[str, Any]:
    with (DATA_DIR / filename).open(encoding="utf-8") as file:
        data: dict[str, Any] = json.load(file)
    return data


@lru_cache(maxsize=1)
def type_chart() -> TypeChart:
    return TypeChart.from_mapping(_load("typechart.json")["chart"])


@lru_cache(maxsize=1)
def item_catalog() -> ItemCatalog:
    return ItemCatalog.from_mapping(_load("items.json")["items"])


@lru_cache(maxsize=1)
def nature_catalog() -> NatureCatalog:
    return NatureCatalog.from_mapping(_load("natures.json")["natures"])


@lru_cache(maxsize=1)
def pokedex() -> Pokedex:
    return Pokedex.from_mapping(_load("pokemon.json")["pokemon"])


@lru_cache(maxsize=1)
def move_dex() -> MoveDex:
    return MoveDex.from_mapping(_load("moves.json")["moves"])


@lru_cache(maxsize=1)
def learnset() -> Learnset:
    return Learnset.from_mapping(_load("learnsets.json")["learnsets"])


@lru_cache(maxsize=1)
def pokemon_name_map() -> NameMap:
    return NameMap.from_mapping(_load("name_map.json")["pokemon"])


@lru_cache(maxsize=1)
def nature_name_map() -> NameMap:
    return NameMap.from_mapping(_load("name_map_natures.json")["natures"])


@lru_cache(maxsize=1)
def ability_name_map() -> NameMap:
    return NameMap.from_mapping(_load("name_map_abilities.json")["abilities"])
