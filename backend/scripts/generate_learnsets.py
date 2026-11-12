"""learnset(포켓몬이 배울 수 있는 기술) 시드를 생성한다 (Phase 2-6).

실행:
    docker compose exec backend uv run python -m scripts.generate_learnsets

출처는 championsbattledata 인덱스의 `learnableMoveNames` 다. **우리가 이미 매번 받는
응답 안에 있었다** — Phase 2-2 에서 "learnset 은 어디에도 없다"고 판단하고 외부 출처를
찾아 헤맸는데, 엔트리 최상위 키를 끝까지 보지 않아서 놓쳤다 (JOURNAL 2026-11-13).

★ 이 데이터가 필요한 이유 ★

파티 빌더(Phase 5)가 "이 포켓몬이 이 기술을 배울 수 있는가"를 검증해야 한다.
검증이 없으면 사용자가 **배울 수 없는 기술이 들어간 파티를 저장**하고, 그 파티로
계산한 데미지를 근거라고 믿게 된다.

★ fail-closed ★

기술 id 가 하나라도 `moves.json` 으로 해석되지 않으면 파일을 쓰지 않는다.
해석 안 되는 쌍만 건너뛰면 "이 포켓몬은 그 기술을 못 배운다"는 **틀린 사실**이
조용히 들어간다. 실제로 이 검사가 기술표의 누락(전기쇼크)을 잡아냈다.
"""

from __future__ import annotations

import http.client
import json
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Final

API_INDEX: Final[str] = "https://championsbattledata.com/api"
USER_AGENT: Final[str] = "pokehelper-dev/0.1 (learnset seed generator)"
REQUEST_DELAY_SECONDS: Final[float] = 1.0
TRANSIENT_RETRIES: Final[int] = 3
RETRY_BACKOFF_SECONDS: Final[float] = 3.0

DATA_DIR: Final[Path] = Path(__file__).resolve().parent.parent / "data"
POKEMON_PATH: Final[Path] = DATA_DIR / "pokemon.json"
MOVES_PATH: Final[Path] = DATA_DIR / "moves.json"
OUTPUT_PATH: Final[Path] = DATA_DIR / "learnsets.json"

LEARNSET_FIELD: Final[str] = "learnableMoveNames"

# 한 포켓몬이 배우는 기술 수의 상식 범위. 벗어나면 출처 형식이 변한 것이다.
MIN_MOVES_PER_POKEMON: Final[int] = 1
MAX_MOVES_PER_POKEMON: Final[int] = 200


class TransientFetchError(Exception):
    """재시도를 다 쓰고도 응답을 못 받았다."""


def to_move_id(text: str) -> str:
    """showdown 식 id. 영숫자만 남긴 소문자."""
    return re.sub(r"[^a-z0-9]", "", text.lower())


def fetch_json(url: str) -> Any:
    last_error: Exception | None = None
    for attempt in range(TRANSIENT_RETRIES):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(request, timeout=180) as response:
                return json.load(response)
        except urllib.error.HTTPError:
            raise
        except (http.client.HTTPException, OSError) as error:
            last_error = error
            time.sleep(RETRY_BACKOFF_SECONDS * (attempt + 1))
    raise TransientFetchError(f"{url}: {TRANSIENT_RETRIES}회 재시도 후 실패 — {last_error}")


def load(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as file:
        data: dict[str, Any] = json.load(file)
    return data


def build(index: dict[str, Any], known_moves: set[str]) -> tuple[dict[str, list[str]], list[str]]:
    errors: list[str] = []
    table: dict[str, list[str]] = {}
    unresolved: dict[str, list[str]] = {}

    for entry in index["pokemon"]:
        showdown_id = str(entry["showdownId"])
        names = entry.get(LEARNSET_FIELD)
        if names is None:
            errors.append(f"{showdown_id}: {LEARNSET_FIELD} 가 없다. 출처 형식이 변했다.")
            continue

        move_ids: list[str] = []
        for name in names:
            move_id = to_move_id(str(name))
            if move_id not in known_moves:
                unresolved.setdefault(move_id, []).append(showdown_id)
                continue
            move_ids.append(move_id)

        table[showdown_id] = sorted(set(move_ids))

    for move_id, owners in sorted(unresolved.items()):
        errors.append(
            f"기술 {move_id!r} 를 기술표에서 찾을 수 없다 ({len(owners)}종이 배운다: "
            f"{owners[:3]}). generate_moves.py 를 먼저 돌려 기술표를 채운다 — "
            "쌍만 건너뛰면 '못 배운다'는 틀린 사실이 들어간다."
        )

    return table, errors


def verify(table: dict[str, list[str]], our_pokemon: set[str]) -> list[str]:
    errors: list[str] = []

    missing = sorted(our_pokemon - set(table))
    if missing:
        errors.append(f"learnset 이 없는 포켓몬 {len(missing)}종: {missing[:10]}")

    for showdown_id in sorted(our_pokemon & set(table)):
        count = len(table[showdown_id])
        if not MIN_MOVES_PER_POKEMON <= count <= MAX_MOVES_PER_POKEMON:
            errors.append(
                f"{showdown_id}: 배우는 기술이 {count}개다. "
                f"{MIN_MOVES_PER_POKEMON}~{MAX_MOVES_PER_POKEMON} 범위를 벗어났다."
            )
    return errors


def main() -> None:
    pokemon = load(POKEMON_PATH)["pokemon"]
    moves = load(MOVES_PATH)["moves"]
    print(f"마스터: 포켓몬 {len(pokemon)}종 / 기술 {len(moves)}개")

    print(f"인덱스 수집: {API_INDEX}")
    index = fetch_json(API_INDEX)

    table, errors = build(index, set(moves))
    # 우리 마스터에 있는 포켓몬만 남긴다. 인덱스가 더 줄 수도 있다.
    table = {key: value for key, value in table.items() if key in pokemon}
    errors += verify(table, set(pokemon))

    if errors:
        print(f"\n검증 실패 {len(errors)}건 — 파일을 쓰지 않는다.", file=sys.stderr)
        for line in errors[:20]:
            print(f"  {line}", file=sys.stderr)
        raise SystemExit(1)

    pairs = sum(len(value) for value in table.values())
    payload = {
        "_meta": {
            "source": API_INDEX,
            "field": LEARNSET_FIELD,
            "generated_by": "backend/scripts/generate_learnsets.py",
            "key": "showdown_id -> 배울 수 있는 기술 id 목록",
            "note": (
                "파티 빌더가 '배울 수 없는 기술'을 거르는 데 쓴다. "
                "기술 id 가 하나라도 moves.json 으로 해석되지 않으면 생성하지 않는다 — "
                "쌍만 건너뛰면 '못 배운다'는 틀린 사실이 들어간다. "
                "이 검사가 실제로 기술표의 누락(전기쇼크)을 잡아냈다."
            ),
            "pokemon": len(table),
            "pairs": pairs,
        },
        "learnsets": dict(sorted(table.items())),
    }

    with OUTPUT_PATH.open("w", encoding="utf-8", newline="\n") as file:
        json.dump(payload, file, ensure_ascii=False, indent=2)
        file.write("\n")

    print(f"\n검증 통과. 포켓몬 {len(table)}종 / 쌍 {pairs}개")
    print(f"  {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
