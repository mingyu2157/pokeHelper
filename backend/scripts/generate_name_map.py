"""포켓몬 한영 명칭 매핑을 생성한다 (Phase 2-3).

실행:
    docker compose exec backend uv run python -m scripts.generate_name_map

★ 이 구간은 오류 허용치가 0이다 (ADR-005) ★

데미지 계산이 틀리면 수치가 이상해서 눈에 띈다. 그런데 이름 매핑이 틀리면 화면에는
멀쩡한 한글 이름이 뜨고 그 뒤에서 **엉뚱한 포켓몬의 종족값으로 계산이 돌아간다.**
사용자도 모르고 우리도 모른다. 그래서 자동 확정 조건을 좁게 잡고, 조금이라도
확신이 없으면 `name_map_pending.json` 으로 보낸다 (fail-closed).

★ 자동 확정 조건 (전부 만족해야 한다) ★

1. 우리 `showdown_id` 에 대응하는 PokéAPI 슬러그 후보가 있다 (smartnuo 가 제공).
2. 그 PokéAPI 엔트리의 **종족값 6개가 우리 값과 완전히 같다.**
3. 그 PokéAPI 엔트리의 **타입이 우리 값과 완전히 같다.**
   → 종족값만으로는 부족하다. 로토무 5폼·켄타로스 3품종처럼 종족값이 같고 타입만
     다른 가족이 있다. 둘을 같이 봐야 "엉뚱한 폼에 이름을 붙이는" 사고를 막는다.
4. 해당 종(species)에 한국어 명칭이 있다.
5. **`form_kind` 가 `Base` 다.**
   → 폼의 공식 표기는 `종명(폼라벨)` 형태라 **문자열 조합**이 필요한데, ADR-005 가
     런타임 조합을 금지한다. 그래서 폼은 전부 사람 검수로 보낸다. 조합한 후보를
     초안으로 제시하고, 검수를 통과한 **확정 문자열만** 매핑에 들어간다.

위 조건을 못 넘긴 항목은 영문명을 통과시키거나 추측하지 않는다. 이유와 후보를
함께 적어 pending 에 남긴다.
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

SMARTNUO_URL: Final[str] = "https://smartnuo.com/api/pokemon"
POKEAPI_POKEMON: Final[str] = "https://pokeapi.co/api/v2/pokemon/{slug}"
POKEAPI_FORM: Final[str] = "https://pokeapi.co/api/v2/pokemon-form/{slug}"

USER_AGENT: Final[str] = "pokehelper-dev/0.1 (name map generator)"
REQUEST_DELAY_SECONDS: Final[float] = 1.0  # CLAUDE.md 8-6
TRANSIENT_RETRIES: Final[int] = 3
RETRY_BACKOFF_SECONDS: Final[float] = 3.0

DATA_DIR: Final[Path] = Path(__file__).resolve().parent.parent / "data"
POKEMON_PATH: Final[Path] = DATA_DIR / "pokemon.json"
NAME_MAP_PATH: Final[Path] = DATA_DIR / "name_map.json"
PENDING_PATH: Final[Path] = DATA_DIR / "name_map_pending.json"

KOREAN: Final[str] = "ko"
BASE_FORM: Final[str] = "Base"

# smartnuo 가 다리를 못 놓는 3개. 우리 form_kind 를 근거로 슬러그를 지정하고,
# 맞는지는 종족값·타입 검증이 판정한다. 틀리면 검증에서 걸린다.
MANUAL_POKEAPI_SLUG: Final[dict[str, str]] = {
    # 우리 form_kind 가 "Shield Forme" 다. 킬가르도는 방패/블레이드의 종족값이
    # 서로 뒤집혀 있어서(50/150 <-> 150/50) 검증이 확실히 구분한다.
    "aegislash": "aegislash-shield",
    # PokéAPI 의 기본 변종이 three 다. four 는 `mausholdfour` 로 따로 있다.
    "maushold": "maushold-family-of-three",
    # 비비용은 무늬가 종족값·타입에 영향을 주지 않고 우리 데이터에 **엔트리가 하나뿐**이라
    # 무늬를 구분할 이유가 없다. 그래서 표기를 '비비용' 하나로 통일한다 (사용자 결정).
    # 슬러그는 신원 검증용으로만 쓴다 — 무늬 간 종족값·타입이 같아 어느 무늬를 써도 같다.
    # (출처끼리 어긋나는 점은 남겨둔다: showdown id 는 fancy, form_kind 는 Icy Snow Pattern.)
    "vivillonfancy": "vivillon",
}

# 검수된 폼 명칭 파일. 폼의 공식 표기는 `종명(폼라벨)` 조합이고 ADR-005 가 런타임 조합을
# 금지하므로, **사람이 검수한 확정 문자열**만 여기 들어온다. 생성기는 조합하지 않고
# 이 파일을 읽어 합치기만 한다 (방어선 6).
REVIEWED_FORMS_PATH: Final[Path] = DATA_DIR / "name_map_forms.json"

STAT_KEY_BY_POKEAPI: Final[dict[str, str]] = {
    "hp": "hp",
    "attack": "attack",
    "defense": "defense",
    "special-attack": "sp_attack",
    "special-defense": "sp_defense",
    "speed": "speed",
}


class TransientFetchError(Exception):
    """재시도를 다 쓰고도 응답을 못 받았다."""


CACHE_DIR: Final[Path] = Path(__file__).resolve().parent.parent / ".cache" / "pokeapi"


def _cache_path(url: str) -> Path:
    return CACHE_DIR / f"{hashlib.sha1(url.encode()).hexdigest()}.json"


def _fetch_json(url: str) -> Any:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=120) as response:
        return json.load(response)


def fetch_cached(url: str) -> Any:
    """디스크 캐시를 거쳐 가져온다. 캐시에 있으면 요청하지 않는다.

    검수 -> 재생성을 여러 번 돌려야 하는 작업이라, 같은 응답을 반복해 받아오면
    출처에 부담만 준다 (CLAUDE.md 8-2 요청량 통제). 캐시는 커밋하지 않는다.
    """
    path = _cache_path(url)
    if path.exists():
        with path.open(encoding="utf-8") as file:
            return json.load(file)
    payload = fetch_json(url)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as file:
        json.dump(payload, file, ensure_ascii=False)
    time.sleep(REQUEST_DELAY_SECONDS)
    return payload


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


def to_id(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def korean_name(names: list[dict[str, Any]], key: str = "name") -> str | None:
    for entry in names:
        if entry["language"]["name"] == KOREAN:
            value = str(entry[key]).strip()
            return value or None
    return None


def build_bridge(ours: dict[str, Any]) -> tuple[dict[str, str], dict[str, str]]:
    """showdown_id -> PokéAPI 슬러그 후보.

    smartnuo 의 `pokeapi_form_name` / `pokeapi_name` 을 후보로 쓴다. **후보일 뿐이다** —
    맞는지는 종족값·타입 검증이 판정한다. 문자열 유사도는 쓰지 않는다 (§8-4).
    """
    records = fetch_cached(SMARTNUO_URL)
    by_en: dict[str, dict[str, Any]] = {}
    by_smogon: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        if record.get("en"):
            by_en[to_id(str(record["en"]))] = record
        if record.get("smogon_name"):
            by_smogon.setdefault(to_id(str(record["smogon_name"])), []).append(record)

    bridge: dict[str, str] = {}
    hints: dict[str, str] = {}  # smartnuo 의 한글명 (검수 참고용, 확정에는 쓰지 않는다)
    for showdown_id in ours:
        record = by_en.get(showdown_id)
        if record is None:
            candidates = by_smogon.get(showdown_id, [])
            # smogon_name 조인은 중복이 있다. 유일할 때만 쓴다.
            record = candidates[0] if len(candidates) == 1 else None

        manual = MANUAL_POKEAPI_SLUG.get(showdown_id)
        if manual:
            bridge[showdown_id] = manual
        elif record is not None:
            slug = record.get("pokeapi_form_name") or record.get("pokeapi_name")
            if slug:
                bridge[showdown_id] = str(slug)

        if record is not None and record.get("kr"):
            hints[showdown_id] = str(record["kr"])
    return bridge, hints


def load_reviewed_forms() -> dict[str, str]:
    """검수가 끝난 폼 명칭을 읽는다. 없으면 빈 매핑이다.

    생성기는 이 문자열을 **그대로** 쓴다. 조합하거나 다듬지 않는다 (ADR-005).
    """
    if not REVIEWED_FORMS_PATH.exists():
        return {}
    with REVIEWED_FORMS_PATH.open(encoding="utf-8") as file:
        payload = json.load(file)

    reviewed: dict[str, str] = {}
    for key, value in payload["pokemon"].items():
        # ★ `confirmed: true` 인 항목만 받아들인다.
        # 이 파일의 `ko` 는 생성기가 만든 **초안**으로 시작한다. 초안을 그대로 받으면
        # "사람 검수"가 그냥 기계 출력에 도장을 찍는 절차가 된다. 플래그를 사람이
        # 뒤집는 행위가 검수다.
        if not value.get("confirmed"):
            continue
        korean = value.get("ko")
        if not korean or not str(korean).strip():
            continue
        reviewed[key] = str(korean).strip()
    return reviewed


def verify_identity(ours: dict[str, Any], pokeapi: dict[str, Any]) -> list[str]:
    """종족값과 타입이 우리 데이터와 같은지 본다. ADR-005 방어선 2."""
    problems: list[str] = []

    their_stats = {
        STAT_KEY_BY_POKEAPI[entry["stat"]["name"]]: int(entry["base_stat"])
        for entry in pokeapi["stats"]
        if entry["stat"]["name"] in STAT_KEY_BY_POKEAPI
    }
    if their_stats != ours["base_stats"]:
        problems.append(f"종족값 불일치 — 우리 {ours['base_stats']} / PokéAPI {their_stats}")

    their_types = [entry["type"]["name"] for entry in sorted(pokeapi["types"], key=_slot)]
    if their_types != list(ours["types"]):
        problems.append(f"타입 불일치 — 우리 {list(ours['types'])} / PokéAPI {their_types}")

    return problems


def _slot(entry: dict[str, Any]) -> int:
    return int(entry["slot"])


def main() -> None:
    with POKEMON_PATH.open(encoding="utf-8") as file:
        ours: dict[str, Any] = json.load(file)["pokemon"]
    print(f"우리 폼 {len(ours)}개")

    print("smartnuo 로 PokéAPI 다리 구성...")
    bridge, hints = build_bridge(ours)
    print(f"  다리 {len(bridge)}/{len(ours)}")

    confirmed: dict[str, dict[str, Any]] = {}
    pending: dict[str, dict[str, Any]] = {}
    errors_reviewed: list[str] = []
    species_cache: dict[str, str | None] = {}

    for position, showdown_id in enumerate(sorted(ours)):
        record = ours[showdown_id]
        slug = bridge.get(showdown_id)
        hint = hints.get(showdown_id)

        def hold(
            reason: str,
            _id: str = showdown_id,
            _record: dict[str, Any] = record,
            _slug: str | None = slug,
            _hint: str | None = hint,
            **extra: Any,
        ) -> None:
            pending[_id] = {
                "reason": reason,
                "form_kind": _record["form_kind"],
                "display_name": _record["display_name"],
                "pokeapi_slug": _slug,
                "smartnuo_ko": _hint,
                **extra,
            }

        if slug is None:
            hold("PokéAPI 슬러그 후보가 없다. 사람이 지정해야 한다.")
            continue

        try:
            pokeapi = fetch_cached(POKEAPI_POKEMON.format(slug=slug))
        except urllib.error.HTTPError as error:
            hold(f"PokéAPI 조회 실패 ({error.code}). 슬러그 후보가 틀렸을 수 있다.")
            continue
        except TransientFetchError as error:
            hold(f"PokéAPI 조회 실패 — {error}")
            continue

        problems = verify_identity(record, pokeapi)
        if problems:
            # ★ 여기서 걸리면 다리가 엉뚱한 포켓몬을 가리키고 있다는 뜻이다.
            hold("신원 검증 실패 — " + " / ".join(problems))
            continue

        species_url = str(pokeapi["species"]["url"])
        if species_url not in species_cache:
            species = fetch_cached(species_url)
            species_cache[species_url] = korean_name(species["names"])
        species_ko = species_cache[species_url]

        if species_ko is None:
            hold("PokéAPI 에 한국어 종 명칭이 없다.")
            continue

        if record["form_kind"] != BASE_FORM:
            # 폼은 자동 확정하지 않는다. 공식 표기가 `종명(폼라벨)` 조합이고
            # ADR-005 가 조합을 금지하므로, 후보만 만들어 검수로 넘긴다.
            form_ko: str | None = None
            try:
                form = fetch_cached(POKEAPI_FORM.format(slug=slug))
                form_ko = korean_name(form.get("form_names", []))
            except (urllib.error.HTTPError, TransientFetchError):
                form_ko = None

            hold(
                "폼이다. 공식 표기는 조합이 필요하므로 검수 후 확정 문자열을 넣는다.",
                species_ko=species_ko,
                pokeapi_form_label_ko=form_ko,
                official_candidate=f"{species_ko}({form_ko})" if form_ko else None,
                verified_identity=True,
            )
            continue

        confirmed[showdown_id] = {
            "ko": species_ko,
            "pokeapi_slug": slug,
            "verified": ["base_stats", "types"],
        }

        if (position + 1) % 50 == 0:
            print(f"  {position + 1}/{len(ours)} 진행 (확정 {len(confirmed)}, 보류 {len(pending)})")

    # ★ 종 명칭만으로 구분이 안 되는 항목을 검수로 내린다.
    #
    # 조건 ⑤(`form_kind == Base` 면 자동 확정)는 출처의 폼 라벨을 믿는 것이었는데,
    # championsbattledata 가 `mausholdfour` 와 `squawkabillyyellow` 를 **Base 로**
    # 표기해서 같은 종 명칭이 두 id 에 붙었다. 출처의 라벨은 믿을 수 없다.
    #
    # 여기서 멈추면(에러) 검수할 기회가 없다. 겹친 항목을 **전부** 내려서
    # 사람이 구분 표기를 정하게 한다. 한쪽만 남기면 어느 쪽이 진짜인지 모른다.
    collisions: dict[str, list[str]] = {}
    for showdown_id, entry in confirmed.items():
        collisions.setdefault(entry["ko"], []).append(showdown_id)
    for korean, ids in sorted(collisions.items()):
        if len(ids) == 1:
            continue
        for showdown_id in ids:
            entry = confirmed.pop(showdown_id)
            record = ours[showdown_id]
            pending[showdown_id] = {
                "reason": (
                    f"종 명칭 {korean!r} 이 {len(ids)}개 id 에 겹친다. 같은 종의 여러 폼이 "
                    "들어와 있어 종 명칭만으로는 구분되지 않는다. 구분 표기가 필요하다."
                ),
                "form_kind": record["form_kind"],
                "display_name": record["display_name"],
                "pokeapi_slug": entry["pokeapi_slug"],
                "smartnuo_ko": hints.get(showdown_id),
                "species_ko": korean,
                "collides_with": [i for i in ids if i != showdown_id],
                "verified_identity": True,
            }

    # 검수된 폼 명칭을 합친다 (ADR-005 방어선 6).
    # **신원 검증을 통과한 폼만** 받아들인다. 검수는 표기를 확정하는 단계지,
    # 종족값·타입 검증을 건너뛰는 통로가 아니다.
    reviewed = load_reviewed_forms()
    for showdown_id, korean in sorted(reviewed.items()):
        held = pending.get(showdown_id)
        if held is None:
            errors_reviewed.append(
                f"{showdown_id}: 검수 파일에 있는데 보류 목록에 없다. "
                "id 가 바뀌었거나 이미 자동 확정됐다."
            )
            continue
        if not held.get("verified_identity"):
            errors_reviewed.append(
                f"{showdown_id}: 신원 검증을 통과하지 않았는데 검수 파일에 들어 있다 "
                f"({held['reason']})"
            )
            continue
        confirmed[showdown_id] = {
            "ko": korean,
            "pokeapi_slug": held["pokeapi_slug"],
            "verified": ["base_stats", "types"],
            "source": "reviewed_form",
        }
        del pending[showdown_id]

    # 양방향 유일성 (ADR-005 방어선 3)
    errors: list[str] = list(errors_reviewed)
    by_ko: dict[str, list[str]] = {}
    for showdown_id, entry in confirmed.items():
        by_ko.setdefault(entry["ko"], []).append(showdown_id)
    for ko, ids in sorted(by_ko.items()):
        if len(ids) > 1:
            errors.append(
                f"한글명 {ko!r} 가 {len(ids)}개 id 에 중복된다: {sorted(ids)}. "
                "검수된 폼 명칭끼리 겹쳤다 — name_map_forms.json 에서 구분해야 한다."
            )

    if errors:
        print(f"\n유일성 검증 실패 {len(errors)}건 — 파일을 쓰지 않는다.", file=sys.stderr)
        for line in errors:
            print(f"  {line}", file=sys.stderr)
        raise SystemExit(1)

    payload = {
        "_meta": {
            "sources": {
                "korean_names": "https://pokeapi.co/api/v2/pokemon-species/{slug}",
                "pokeapi_bridge": SMARTNUO_URL,
                "identity_check": "https://pokeapi.co/api/v2/pokemon/{slug}",
            },
            "generated_by": "backend/scripts/generate_name_map.py",
            "key": "showdown_id -> 한국 서버 명칭",
            "note": (
                "자동 확정 조건: 종족값 6개 일치 + 타입 일치 + 종 한국어명 존재 + Base 폼. "
                "폼은 공식 표기가 조합이 필요해 사람 검수를 거친다 (ADR-005). "
                "퍼지 매칭 금지 — 정확 일치 조회만 한다."
            ),
            "confirmed": len(confirmed),
            "pending": len(pending),
            "total": len(ours),
        },
        "pokemon": dict(sorted(confirmed.items())),
    }
    pending_payload = {
        "_meta": {
            "generated_by": "backend/scripts/generate_name_map.py",
            "note": (
                "자동 확정 조건을 못 넘긴 항목. **영문명을 통과시키지 않는다** — "
                "검수해서 확정 문자열을 name_map.json 에 넣을 때까지 미매핑으로 남는다. "
                "official_candidate 는 초안이다. 그대로 믿지 말고 확인한다."
            ),
            "count": len(pending),
        },
        "pokemon": dict(sorted(pending.items())),
    }

    for path, data in ((NAME_MAP_PATH, payload), (PENDING_PATH, pending_payload)):
        with path.open("w", encoding="utf-8", newline="\n") as file:
            json.dump(data, file, ensure_ascii=False, indent=2)
            file.write("\n")

    print(f"\n확정 {len(confirmed)} / 보류 {len(pending)} / 전체 {len(ours)}")
    print(f"  {NAME_MAP_PATH}")
    print(f"  {PENDING_PATH}")
    if pending:
        print("\n보류 항목은 사람 검수가 필요하다. 커버리지 테스트는 보류가 남아 있으면 깨진다.")


if __name__ == "__main__":
    main()
