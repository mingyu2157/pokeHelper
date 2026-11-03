"""PokéAPI 에서 타입 상성표를 생성해 `backend/data/typechart.json` 으로 쓴다.

사람이 324칸을 손으로 적지 않기 위한 스크립트다. 기억에 의존하면 한 칸이 틀려도
테스트가 잡지 못하고, 그 타입이 걸린 데미지 계산이 전부 조용히 틀어진다.

실행:
    cd backend && python -m scripts.generate_typechart

이 스크립트는 개발 도구이지 런타임 경로가 아니다. 생성 결과를 커밋해서 고정하고,
서비스는 커밋된 JSON 만 읽는다. ADR-005 에서 `name_map.json` 을 고정한 것과 같은 이유다.

출처: https://pokeapi.co/api/v2/type/{name}  (damage_relations)
전제: 챔피언스의 타입 상성은 본가와 동일하다 (2026-11-03 개발자 확인).
"""

from __future__ import annotations

import json
import time
import urllib.request
from pathlib import Path
from typing import Any, Final

API_BASE: Final[str] = "https://pokeapi.co/api/v2/type"
USER_AGENT: Final[str] = "pokehelper-dev/0.1 (typechart generator)"
REQUEST_DELAY_SECONDS: Final[float] = 0.5

# 본가 18타입. PokéAPI 의 'unknown' / 'shadow' 는 제외한다.
TYPES: Final[tuple[str, ...]] = (
    "normal",
    "fighting",
    "flying",
    "poison",
    "ground",
    "rock",
    "bug",
    "ghost",
    "steel",
    "fire",
    "water",
    "grass",
    "electric",
    "psychic",
    "ice",
    "dragon",
    "dark",
    "fairy",
)

NEUTRAL: Final[float] = 1.0
OUTPUT_PATH: Final[Path] = Path(__file__).resolve().parent.parent / "data" / "typechart.json"


def fetch_type(name: str) -> dict[str, Any]:
    request = urllib.request.Request(f"{API_BASE}/{name}", headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=30) as response:
        data: dict[str, Any] = json.load(response)
    return data


def _names(relations: dict[str, Any], key: str) -> list[str]:
    return [entry["name"] for entry in relations[key]]


def build_chart(raw: dict[str, dict[str, Any]]) -> dict[str, dict[str, float]]:
    """공격 타입 -> 방어 타입 -> 배율. 1.0(중립)은 담지 않는다."""
    chart: dict[str, dict[str, float]] = {}
    for attacker in TYPES:
        relations = raw[attacker]["damage_relations"]
        row: dict[str, float] = {}
        for defender in _names(relations, "double_damage_to"):
            row[defender] = 2.0
        for defender in _names(relations, "half_damage_to"):
            row[defender] = 0.5
        for defender in _names(relations, "no_damage_to"):
            row[defender] = 0.0
        chart[attacker] = dict(sorted(row.items()))
    return chart


def verify_mirror(raw: dict[str, dict[str, Any]], chart: dict[str, dict[str, float]]) -> list[str]:
    """`*_to` 로 만든 표를 `*_from` 으로 역검증한다.

    PokéAPI 는 같은 관계를 공격 측과 방어 측 양쪽에 기록한다.
    두 방향이 어긋나면 수집이나 가정이 잘못된 것이므로 생성을 중단한다.
    """
    errors: list[str] = []
    for defender in TYPES:
        relations = raw[defender]["damage_relations"]
        expected: dict[str, float] = {}
        for attacker in _names(relations, "double_damage_from"):
            expected[attacker] = 2.0
        for attacker in _names(relations, "half_damage_from"):
            expected[attacker] = 0.5
        for attacker in _names(relations, "no_damage_from"):
            expected[attacker] = 0.0

        for attacker in TYPES:
            from_to = chart[attacker].get(defender, NEUTRAL)
            from_from = expected.get(attacker, NEUTRAL)
            if from_to != from_from:
                errors.append(
                    f"{attacker} -> {defender}: *_to 기준 {from_to}, *_from 기준 {from_from}"
                )
    return errors


def main() -> None:
    raw: dict[str, dict[str, Any]] = {}
    for index, name in enumerate(TYPES, start=1):
        print(f"[{index:2d}/{len(TYPES)}] {name}")
        raw[name] = fetch_type(name)
        time.sleep(REQUEST_DELAY_SECONDS)

    chart = build_chart(raw)

    errors = verify_mirror(raw, chart)
    if errors:
        print(f"\n양방향 검증 실패 {len(errors)}건 — 파일을 쓰지 않는다.")
        for line in errors[:20]:
            print(f"  {line}")
        raise SystemExit(1)

    entries = sum(len(row) for row in chart.values())
    payload = {
        "_meta": {
            "source": "https://pokeapi.co/api/v2/type",
            "generated_by": "backend/scripts/generate_typechart.py",
            "note": (
                "1.0(중립) 관계는 담지 않는다. 조회 시 기본값 1.0 을 쓴다. "
                "챔피언스 타입 상성은 본가와 동일하다는 전제로 생성했다."
            ),
            "type_count": len(TYPES),
            "non_neutral_entries": entries,
        },
        "types": list(TYPES),
        "chart": chart,
    }

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT_PATH.open("w", encoding="utf-8", newline="\n") as file:
        json.dump(payload, file, ensure_ascii=False, indent=2)
        file.write("\n")

    print(f"\n양방향 검증 통과. 비중립 {entries}칸 -> {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
