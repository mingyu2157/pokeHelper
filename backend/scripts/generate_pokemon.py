"""championsbattledata 인덱스에서 포켓몬 마스터 시드를 생성한다.

실행:
    docker compose exec backend uv run python -m scripts.generate_pokemon

★ 이 스크립트가 존재하는 가장 큰 이유 ★

championsbattledata 가 `hp` / `attack` / ... 이라고 부르는 값은 **종족값이 아니다.**
**노력치 0 · 무보정 상태의 실수치**다. 즉 우리 공식(CLAUDE.md 5-1)의 오프셋이
이미 더해져 있다.

    API 의 hp      = 종족값 + 75
    API 의 그 외    = 종족값 + 20

그대로 `BaseStats` 에 넣으면 `calc_hp()` / `calc_stat()` 이 오프셋을 한 번 더
더해서 모든 계산이 틀어진다. 그래서 여기서 벗겨내고, 벗겨낸 값이 정상 범위인지
검증한 뒤에만 파일을 쓴다.

근거 (2026-11-08 확인):
    garchomp  API 183/150/115/100/105/122
              본가 108/130/ 95/ 80/ 85/102
              차이  +75/+20/+20/+20/+20/+20
    무작위 20종을 PokéAPI 종족값과 대조해 전부 일치함을 확인했다.
    즉 **챔피언스 종족값은 본가와 같다.**

한국어 명칭은 담지 않는다. 한영 매핑은 `name_map.json` 이 담당한다 (ADR-005).
"""

from __future__ import annotations

import json
import urllib.request
from pathlib import Path
from typing import Any, Final

API_INDEX: Final[str] = "https://championsbattledata.com/api"
USER_AGENT: Final[str] = "pokehelper-dev/0.1 (pokemon seed generator)"

# API 값에서 벗겨낼 오프셋. CLAUDE.md 5-1 의 축약 공식과 같은 수다.
HP_OFFSET: Final[int] = 75
STAT_OFFSET: Final[int] = 20

STAT_KEYS: Final[tuple[tuple[str, int], ...]] = (
    ("hp", HP_OFFSET),
    ("attack", STAT_OFFSET),
    ("defense", STAT_OFFSET),
    ("sp_attack", STAT_OFFSET),
    ("sp_defense", STAT_OFFSET),
    ("speed", STAT_OFFSET),
)
TOTAL_OFFSET: Final[int] = HP_OFFSET + STAT_OFFSET * 5

MIN_BASE_STAT: Final[int] = 1
MAX_BASE_STAT: Final[int] = 255
MAX_TYPES: Final[int] = 2

VALID_TYPES: Final[frozenset[str]] = frozenset(
    {
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
    }
)

OUTPUT_PATH: Final[Path] = Path(__file__).resolve().parent.parent / "data" / "pokemon.json"


def fetch_index() -> dict[str, Any]:
    request = urllib.request.Request(API_INDEX, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=120) as response:
        data: dict[str, Any] = json.load(response)
    return data


def build(index: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """엔트리당 `summary.primary` 하나를 마스터 레코드로 삼는다.

    `summary.forms` 는 형제 폼까지 전부 싣고 있어 엔트리 간 중복이 생긴다
    (447개 중 56개가 중복 이름). `showdownId` 는 263개 전부 유일하고
    CLAUDE.md 8-1 이 지정한 조인 키이기도 하다.
    """
    table: dict[str, dict[str, Any]] = {}
    for entry in index["pokemon"]:
        primary = entry["summary"]["primary"]
        table[entry["showdownId"]] = {
            "name": entry["name"],
            "display_name": primary["saved_name"],
            "form_kind": primary.get("form_kind") or "Base",
            "types": [t.lower() for t in primary["types"]],
            "base_stats": {key: primary[key] - offset for key, offset in STAT_KEYS},
            "abilities": [a for a in (primary.get("abilities") or "").split("|") if a],
            "api_stat_total": primary["base_stat_total"],
        }
    return table


def verify(table: dict[str, dict[str, Any]]) -> list[str]:
    """불변식. 하나라도 깨지면 출처가 변했거나 가정이 틀린 것이다."""
    errors: list[str] = []

    if not table:
        errors.append("포켓몬이 하나도 없다")

    for showdown_id, record in table.items():
        stats = record["base_stats"]

        for key, _ in STAT_KEYS:
            value = stats[key]
            if not MIN_BASE_STAT <= value <= MAX_BASE_STAT:
                errors.append(
                    f"{showdown_id}.{key}: 오프셋을 벗긴 종족값이 범위를 벗어났다 "
                    f"({value}). API 가 종족값을 직접 주기 시작했을 수 있다."
                )

        # 합계 교차검증 — 오프셋을 제대로 벗겼는지 확인하는 가장 강력한 장치
        expected_total = record["api_stat_total"] - TOTAL_OFFSET
        if sum(stats.values()) != expected_total:
            errors.append(
                f"{showdown_id}: 종족값 합 {sum(stats.values())} != "
                f"API 합계 - {TOTAL_OFFSET} = {expected_total}"
            )

        types = record["types"]
        if not 1 <= len(types) <= MAX_TYPES:
            errors.append(f"{showdown_id}: 타입은 1~2개다. 받은 수: {len(types)}")
        for type_name in types:
            if type_name not in VALID_TYPES:
                errors.append(f"{showdown_id}: 알 수 없는 타입 {type_name!r}")
        if len(set(types)) != len(types):
            errors.append(f"{showdown_id}: 같은 타입이 중복된다 {types}")

        if not record["abilities"]:
            errors.append(f"{showdown_id}: 특성이 하나도 없다")

    return errors


def main() -> None:
    print("인덱스 수집 중...")
    index = fetch_index()
    table = build(index)
    print(f"  엔트리 {len(table)}개")

    errors = verify(table)
    if errors:
        print(f"\n검증 실패 {len(errors)}건 — 파일을 쓰지 않는다.")
        for line in errors[:20]:
            print(f"  {line}")
        raise SystemExit(1)

    # api_stat_total 은 검증용이었으므로 출력에서 뺀다.
    for record in table.values():
        del record["api_stat_total"]

    payload = {
        "_meta": {
            "source": API_INDEX,
            "generated_by": "backend/scripts/generate_pokemon.py",
            "note": (
                "API 의 hp/attack/... 는 종족값이 아니라 노력치 0·무보정 실수치다. "
                f"HP 는 -{HP_OFFSET}, 나머지는 -{STAT_OFFSET} 하여 종족값으로 환원해 저장했다. "
                "한국어 명칭은 담지 않는다 (ADR-005, name_map.json 소관)."
            ),
            "key": "showdownId (CLAUDE.md 8-1 의 조인 키)",
            "count": len(table),
        },
        "pokemon": dict(sorted(table.items())),
    }

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT_PATH.open("w", encoding="utf-8", newline="\n") as file:
        json.dump(payload, file, ensure_ascii=False, indent=2)
        file.write("\n")

    print(f"\n검증 통과. {len(table)}종 -> {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
