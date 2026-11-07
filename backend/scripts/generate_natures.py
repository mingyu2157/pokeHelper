"""PokéAPI 에서 성격 25종 표를 생성해 `backend/data/natures.json` 으로 쓴다.

성격은 상승·하락 스탯 쌍이 전부지만, 25행을 손으로 적으면 한 줄이 틀려도
테스트가 잡지 못한다. 타입 상성 때와 같은 이유로 생성한다
(`scripts/generate_typechart.py` 참조 — 당시 스팟 체크에서 실제로 1건을 틀렸다).

실행:
    cd backend && python -m scripts.generate_natures

한국어 명칭은 **담지 않는다.** 한영 매핑은 `name_map.json` 이 담당하고
(ADR-005), 두 곳에 두면 진실의 원천이 갈린다.

출처: https://pokeapi.co/api/v2/nature
전제: 챔피언스의 성격 체계는 본가와 같다 (2026-11-10 개발자 확인).
"""

from __future__ import annotations

import json
import time
import urllib.request
from pathlib import Path
from typing import Any, Final

API_INDEX: Final[str] = "https://pokeapi.co/api/v2/nature?limit=100"
USER_AGENT: Final[str] = "pokehelper-dev/0.1 (nature table generator)"
REQUEST_DELAY_SECONDS: Final[float] = 0.2

EXPECTED_COUNT: Final[int] = 25
EXPECTED_NEUTRAL_COUNT: Final[int] = 5

# PokéAPI 의 스탯 이름 -> 우리 StatKey 식별자
STAT_IDS: Final[dict[str, str]] = {
    "attack": "ATTACK",
    "defense": "DEFENSE",
    "special-attack": "SP_ATTACK",
    "special-defense": "SP_DEFENSE",
    "speed": "SPEED",
}

OUTPUT_PATH: Final[Path] = Path(__file__).resolve().parent.parent / "data" / "natures.json"


def fetch(url: str) -> dict[str, Any]:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=30) as response:
        data: dict[str, Any] = json.load(response)
    return data


def build() -> dict[str, dict[str, str | None]]:
    index = fetch(API_INDEX)
    table: dict[str, dict[str, str | None]] = {}
    for entry in index["results"]:
        detail = fetch(entry["url"])
        raised = (detail.get("increased_stat") or {}).get("name")
        lowered = (detail.get("decreased_stat") or {}).get("name")
        table[detail["name"]] = {
            "raised": STAT_IDS[raised] if raised else None,
            "lowered": STAT_IDS[lowered] if lowered else None,
        }
        print(f"  {detail['name']:10s} {raised or '-':15s} {lowered or '-'}")
        time.sleep(REQUEST_DELAY_SECONDS)
    return table


def verify(table: dict[str, dict[str, str | None]]) -> list[str]:
    """성격 체계의 불변식. 하나라도 깨지면 출처가 변한 것이다."""
    errors: list[str] = []

    if len(table) != EXPECTED_COUNT:
        errors.append(f"성격은 {EXPECTED_COUNT}종이어야 한다. 받은 수: {len(table)}")

    neutral = [n for n, s in table.items() if s["raised"] is None]
    if len(neutral) != EXPECTED_NEUTRAL_COUNT:
        errors.append(
            f"무보정 성격은 {EXPECTED_NEUTRAL_COUNT}종이어야 한다. 받은 수: {len(neutral)}"
        )

    for name, spec in table.items():
        raised, lowered = spec["raised"], spec["lowered"]
        # 항상 쌍이다 — 하나를 올리면 반드시 하나를 내린다
        if (raised is None) != (lowered is None):
            errors.append(f"{name}: 상승·하락이 쌍이 아니다 ({raised} / {lowered})")
        if raised is not None and raised == lowered:
            errors.append(f"{name}: 같은 스탯을 올리고 내린다 ({raised})")
        if "HP" in (raised, lowered):
            errors.append(f"{name}: HP 는 성격 보정을 받지 않는다")

    # 비중립 조합은 유일해야 한다. 상승 5종 × 하락 4종(자기 자신 제외) = 20.
    # 무보정 5종은 전부 (None, None) 이라 이 검사에서 제외한다.
    combos = [(s["raised"], s["lowered"]) for s in table.values() if s["raised"] is not None]
    if len(set(combos)) != len(combos):
        errors.append(f"비중립 조합이 중복된다: {len(combos)}건 중 {len(set(combos))}종류")
    expected_pairs = (len(STAT_IDS) - 1) * len(STAT_IDS)
    if len(combos) != expected_pairs:
        errors.append(f"비중립 성격은 {expected_pairs}종이어야 한다. 받은 수: {len(combos)}")

    return errors


def main() -> None:
    table = build()

    errors = verify(table)
    if errors:
        print(f"\n검증 실패 {len(errors)}건 — 파일을 쓰지 않는다.")
        for line in errors:
            print(f"  {line}")
        raise SystemExit(1)

    payload = {
        "_meta": {
            "source": "https://pokeapi.co/api/v2/nature",
            "generated_by": "backend/scripts/generate_natures.py",
            "note": (
                "한국어 명칭은 담지 않는다. 한영 매핑은 name_map.json 이 담당한다 (ADR-005). "
                "raised/lowered 가 둘 다 null 이면 무보정 성격이다."
            ),
            "count": len(table),
            "neutral_count": sum(1 for s in table.values() if s["raised"] is None),
        },
        "natures": dict(sorted(table.items())),
    }

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT_PATH.open("w", encoding="utf-8", newline="\n") as file:
        json.dump(payload, file, ensure_ascii=False, indent=2)
        file.write("\n")

    print(f"\n검증 통과. {len(table)}종 -> {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
