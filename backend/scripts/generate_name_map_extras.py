"""성격·특성 한영 명칭 매핑을 생성한다 (Phase 2-3b).

실행:
    docker compose exec backend uv run python -m scripts.generate_name_map_extras

포켓몬 매핑(`generate_name_map.py`)과 같은 원칙이다 — 교차검증을 통과한 것만 확정하고,
못 넘긴 항목은 영문명을 통과시키지 않는다 (ADR-005 fail-closed).

★ 무엇으로 교차검증하는가 ★

ADR-005 의 2단계는 "종족값 교차검증"이었다. 성격·특성에는 종족값이 없다. 그래서
**각 대상이 가진 다른 사실**을 검증 재료로 쓴다.

| 대상 | 검증 재료 | 막는 사고 |
|---|---|---|
| 성격 | 상승/하락 스탯 **쌍** | '고집'에 엉뚱한 한글명이 붙는 것 |
| 특성 | 그 특성을 **가진 포켓몬 목록** | '거친피부'를 다른 특성에 붙이는 것 |

성격은 스탯 쌍이 20가지 모두 달라서(무보정 5개는 둘 다 없음) 쌍이 맞으면 그 성격이
맞다. 특성은 "이 특성을 가진 포켓몬"이 PokéAPI 와 우리 데이터에서 겹쳐야 한다 —
우리 `name_map.json` 이 포켓몬별 PokéAPI 슬러그를 들고 있어서 ID 로 대조할 수 있다.

두 파일을 한 스크립트에서 만든다. 수집·캐시·검증 골격이 같고 같은 작업 단위이기 때문이다.
"""

from __future__ import annotations

import hashlib
import http.client
import json
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Final

POKEAPI_NATURE: Final[str] = "https://pokeapi.co/api/v2/nature/{slug}"
POKEAPI_ABILITY: Final[str] = "https://pokeapi.co/api/v2/ability/{slug}"

USER_AGENT: Final[str] = "pokehelper-dev/0.1 (name map generator)"
REQUEST_DELAY_SECONDS: Final[float] = 1.0  # CLAUDE.md 8-6
TRANSIENT_RETRIES: Final[int] = 3
RETRY_BACKOFF_SECONDS: Final[float] = 3.0

BACKEND_DIR: Final[Path] = Path(__file__).resolve().parent.parent
DATA_DIR: Final[Path] = BACKEND_DIR / "data"
CACHE_DIR: Final[Path] = BACKEND_DIR / ".cache" / "pokeapi"

NATURES_PATH: Final[Path] = DATA_DIR / "natures.json"
POKEMON_PATH: Final[Path] = DATA_DIR / "pokemon.json"
POKEMON_NAME_MAP_PATH: Final[Path] = DATA_DIR / "name_map.json"
NATURE_OUT_PATH: Final[Path] = DATA_DIR / "name_map_natures.json"
ABILITY_OUT_PATH: Final[Path] = DATA_DIR / "name_map_abilities.json"

KOREAN: Final[str] = "ko"

# `natures.json` 은 상승/하락을 대문자 StatKey 로 담는다. PokéAPI 는 슬러그를 쓴다.
STAT_SLUG_BY_KEY: Final[dict[str, str]] = {
    "ATTACK": "attack",
    "DEFENSE": "defense",
    "SP_ATTACK": "special-attack",
    "SP_DEFENSE": "special-defense",
    "SPEED": "speed",
}


class TransientFetchError(Exception):
    """재시도를 다 쓰고도 응답을 못 받았다."""


def to_id(text: str) -> str:
    """showdown 식 id. 영숫자만 남긴 소문자.

    사용률 API 는 성격·특성을 영문 표시명("Jolly", "Rough Skin")으로 준다.
    기술과 같은 규칙으로 환원해야 조인이 된다.
    """
    return re.sub(r"[^a-z0-9]", "", text.lower())


def to_slug(text: str) -> str:
    """PokéAPI 슬러그. 영숫자 외는 하이픈으로 바꾼다.

    결정적 정규화다 — 유사도 매칭이 아니다 (§8-4). 틀린 슬러그는 404 로 드러나고,
    맞는 슬러그여도 교차검증을 따로 통과해야 한다.
    """
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]", "-", text.lower())).strip("-")


def _fetch_json(url: str) -> Any:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=120) as response:
        return json.load(response)


def fetch_json(url: str) -> Any:
    """전송 오류만 재시도한다. HTTP 상태 오류는 그대로 올린다."""
    last_error: Exception | None = None
    for attempt in range(TRANSIENT_RETRIES):
        try:
            return _fetch_json(url)
        except urllib.error.HTTPError:
            raise
        except (http.client.HTTPException, OSError) as error:
            last_error = error
            time.sleep(RETRY_BACKOFF_SECONDS * (attempt + 1))
    raise TransientFetchError(f"{url}: {TRANSIENT_RETRIES}회 재시도 후 실패 — {last_error}")


def fetch_cached(url: str) -> Any:
    """디스크 캐시를 거쳐 가져온다. 캐시는 커밋하지 않는다 (CLAUDE.md 8-2 요청량 통제)."""
    path = CACHE_DIR / f"{hashlib.sha1(url.encode()).hexdigest()}.json"
    if path.exists():
        with path.open(encoding="utf-8") as file:
            return json.load(file)
    payload = fetch_json(url)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as file:
        json.dump(payload, file, ensure_ascii=False)
    time.sleep(REQUEST_DELAY_SECONDS)
    return payload


def korean_name(names: list[dict[str, Any]]) -> str | None:
    for entry in names:
        if entry["language"]["name"] == KOREAN:
            value = str(entry["name"]).strip()
            return value or None
    return None


def load(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as file:
        data: dict[str, Any] = json.load(file)
    return data


def build_natures() -> tuple[dict[str, dict[str, Any]], list[str]]:
    """성격 매핑. 상승/하락 스탯 쌍이 우리 데이터와 같아야 확정한다."""
    ours = load(NATURES_PATH)["natures"]
    confirmed: dict[str, dict[str, Any]] = {}
    errors: list[str] = []

    for name, record in sorted(ours.items()):
        url = POKEAPI_NATURE.format(slug=to_slug(name))
        try:
            payload = fetch_cached(url)
        except (urllib.error.HTTPError, TransientFetchError) as error:
            errors.append(f"성격 {name}: 조회 실패 — {error}")
            continue

        their_up = (payload.get("increased_stat") or {}).get("name")
        their_down = (payload.get("decreased_stat") or {}).get("name")
        our_up = STAT_SLUG_BY_KEY.get(record["raised"]) if record["raised"] else None
        our_down = STAT_SLUG_BY_KEY.get(record["lowered"]) if record["lowered"] else None

        if (their_up, their_down) != (our_up, our_down):
            # 여기서 걸리면 엉뚱한 성격의 이름을 붙이려던 것이다.
            errors.append(
                f"성격 {name}: 보정 쌍이 다르다 — 우리 {our_up}/{our_down} / "
                f"PokéAPI {their_up}/{their_down}"
            )
            continue

        korean = korean_name(payload["names"])
        if korean is None:
            errors.append(f"성격 {name}: PokéAPI 에 한국어명이 없다")
            continue

        confirmed[to_id(name)] = {
            "ko": korean,
            "pokeapi_slug": to_slug(name),
            "verified": ["nature_stat_pair"],
        }
    return confirmed, errors


def build_abilities() -> tuple[dict[str, dict[str, Any]], list[str]]:
    """특성 매핑. 그 특성을 가진 포켓몬이 PokéAPI 와 겹쳐야 확정한다."""
    pokemon = load(POKEMON_PATH)["pokemon"]
    name_map = load(POKEMON_NAME_MAP_PATH)["pokemon"]

    # 특성 영문명 -> 그 특성을 가진 우리 포켓몬의 PokéAPI 슬러그
    owner_slugs: dict[str, set[str]] = {}
    for showdown_id, record in pokemon.items():
        entry = name_map.get(showdown_id)
        if entry is None:
            continue  # 포켓몬 매핑이 비어 있으면 그쪽 커버리지 테스트가 잡는다
        for ability in record["abilities"]:
            owner_slugs.setdefault(ability, set()).add(str(entry["pokeapi_slug"]))

    confirmed: dict[str, dict[str, Any]] = {}
    errors: list[str] = []

    for ability, slugs in sorted(owner_slugs.items()):
        url = POKEAPI_ABILITY.format(slug=to_slug(ability))
        try:
            payload = fetch_cached(url)
        except urllib.error.HTTPError as error:
            errors.append(
                f"특성 {ability!r}: PokéAPI 에 없다 (HTTP {error.code}, "
                f"slug={to_slug(ability)}). 챔피언스/ZA 전용이면 사람이 확정해야 한다."
            )
            continue
        except TransientFetchError as error:
            errors.append(f"특성 {ability!r}: 조회 실패 — {error}")
            continue

        their_owners = {str(entry["pokemon"]["name"]) for entry in payload["pokemon"]}
        overlap = slugs & their_owners
        if not overlap:
            # 이름만 맞고 내용이 다른 특성일 수 있다. 겹치는 포켓몬이 하나도 없으면
            # 우리가 아는 그 특성이 맞다고 말할 근거가 없다.
            errors.append(
                f"특성 {ability!r}: 보유 포켓몬이 하나도 겹치지 않는다. "
                f"우리 {sorted(slugs)[:3]} / PokéAPI {sorted(their_owners)[:3]}"
            )
            continue

        korean = korean_name(payload["names"])
        if korean is None:
            errors.append(f"특성 {ability!r}: PokéAPI 에 한국어명이 없다")
            continue

        confirmed[to_id(ability)] = {
            "ko": korean,
            "en": ability,
            "pokeapi_slug": to_slug(ability),
            "verified": ["ability_owners"],
            "owner_overlap": len(overlap),
        }
    return confirmed, errors


def check_uniqueness(label: str, table: dict[str, dict[str, Any]]) -> list[str]:
    """ko -> id 유일성 (ADR-005 방어선 3)."""
    errors: list[str] = []
    by_ko: dict[str, list[str]] = {}
    for key, entry in table.items():
        by_ko.setdefault(entry["ko"], []).append(key)
    for korean, keys in sorted(by_ko.items()):
        if len(keys) > 1:
            errors.append(f"{label} 한글명 {korean!r} 가 {len(keys)}개 id 에 중복: {sorted(keys)}")
    return errors


def write(path: Path, label: str, table: dict[str, dict[str, Any]], verified: str) -> None:
    payload = {
        "_meta": {
            "source": "https://pokeapi.co/api/v2/",
            "generated_by": "backend/scripts/generate_name_map_extras.py",
            "key": f"showdown 식 {label} id (영숫자만 남긴 소문자)",
            "note": (
                f"{label} 한영 매핑. 교차검증({verified})을 통과한 항목만 들어 있다. "
                "퍼지 매칭 금지 — 정확 일치 조회만 한다 (ADR-005)."
            ),
            "count": len(table),
        },
        label: dict(sorted(table.items())),
    }
    with path.open("w", encoding="utf-8", newline="\n") as file:
        json.dump(payload, file, ensure_ascii=False, indent=2)
        file.write("\n")


def main() -> None:
    print("성격 매핑...")
    natures, nature_errors = build_natures()
    print(f"  확정 {len(natures)}")

    print("특성 매핑...")
    abilities, ability_errors = build_abilities()
    print(f"  확정 {len(abilities)}")

    errors = nature_errors + ability_errors
    errors += check_uniqueness("성격", natures)
    errors += check_uniqueness("특성", abilities)

    if errors:
        print(f"\n검증 실패 {len(errors)}건 — 파일을 쓰지 않는다.", file=sys.stderr)
        for line in errors[:40]:
            print(f"  {line}", file=sys.stderr)
        raise SystemExit(1)

    write(NATURE_OUT_PATH, "natures", natures, "상승/하락 스탯 쌍")
    write(ABILITY_OUT_PATH, "abilities", abilities, "보유 포켓몬 목록")

    print(f"\n검증 통과. 성격 {len(natures)} / 특성 {len(abilities)}")
    print(f"  {NATURE_OUT_PATH}")
    print(f"  {ABILITY_OUT_PATH}")


if __name__ == "__main__":
    main()
