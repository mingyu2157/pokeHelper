"""챔피언스 기술 마스터 시드를 생성한다 (Phase 2-2).

실행:
    docker compose exec backend uv run python -m scripts.generate_moves
    docker compose exec backend uv run python -m scripts.generate_moves --skip-usage-join

★ 왜 PokéAPI 가 아닌가 ★

종족값은 본가와 같았지만 **기술 수치는 다르다.** 챔피언스는 밸런스 패치로 위력과
명중을 계속 조정하고, PP 는 {1, 8, 12, 16, 20} 다섯 값으로 정규화했다 (본가는
5~40 제각각). 드래곤클로·크로스포이즌은 '베기' 기술로 재분류됐다.

    부리캐논    본가 100 -> 챔피언스 120
    스타어설트  본가 150 -> 챔피언스 170
    10만볼트    본가 PP 15 -> 챔피언스 PP 16
    강철날개    본가 PP 25 -> 챔피언스 PP 20

본가 데이터(PokéAPI / Showdown 파생 데이터셋)를 쓰면 **틀린 수치가 조용히 들어온다.**
championsbattledata 의 종족값을 실수치로 착각했던 것과 같은 함정이다 (ADR-009).

★ 출처 세 곳 ★

1. op.gg (주 출처) — 패치가 반영된 유일한 기계판독 출처. 515개.
   사용 조건: 헬프센터가 크롤링을 금지하지 않지만 **조건부**다 — 출처 표기와 요청량.
   https://help.op.gg/hc/ko/articles/31091405109401
   **갱신되는 문서이므로 이 스크립트를 돌릴 때 정책 페이지를 같이 확인한다** (ADR-009).
2. champions-helper (교차검증) — 출시 패치 **전** 스냅샷. 위력 18건이 다른데,
   그 18건이 전부 알려진 패치 목록과 일치한다. 그래서 `EXPECTED_DIFFS` 로 고정하고
   **목록 밖 불일치가 하나라도 생기면 생성을 중단한다.**
3. smartnuo (교차검증) — 기술 한글명 892개. 수치는 없다.

한글명은 `moves.json` 에 담지 않는다 (ADR-005). `name_map_moves.json` 으로 분리한다.

수집이 아니라 **시드 생성**이다. 서비스 런타임은 이 사이트들을 호출하지 않는다.
밸런스 패치가 나올 때만 사람이 이 스크립트를 다시 돌린다 (CLAUDE.md 8-3, ADR-009).
"""

from __future__ import annotations

import argparse
import http.client
import json
import re
import sys
import time
import urllib.error
import urllib.request
from html import unescape
from pathlib import Path
from typing import Any, Final

# ---------------------------------------------------------------- 출처

OPGG_URL: Final[str] = "https://op.gg/ko/pokemon-champions/moves"
CHAMPIONS_HELPER_URL: Final[str] = "https://champions-helper.com/data/moves.json"
SMARTNUO_URL: Final[str] = "https://smartnuo.com/api/move"
BATTLE_DATA_INDEX: Final[str] = "https://championsbattledata.com/api"
BATTLE_DATA_ROW: Final[str] = "https://championsbattledata.com/api/battle/Singles/{showdown_id}"

USER_AGENT: Final[str] = "pokehelper-dev/0.1 (move seed generator)"
REQUEST_DELAY_SECONDS: Final[float] = 1.0  # CLAUDE.md 8-6: 요청 간 최소 1초, 동시성 1
TRANSIENT_RETRIES: Final[int] = 3
RETRY_BACKOFF_SECONDS: Final[float] = 3.0

DATA_DIR: Final[Path] = Path(__file__).resolve().parent.parent / "data"
MOVES_PATH: Final[Path] = DATA_DIR / "moves.json"
NAME_MAP_PATH: Final[Path] = DATA_DIR / "name_map_moves.json"

# ---------------------------------------------------------------- 매핑 사전
# §8-4: 퍼지 매칭 금지. 검수된 확정 문자열만 넣는다.
# 전수 열거해서 만든 사전이다. op.gg 가 값을 하나라도 추가하면 생성이 실패한다.

TYPE_BY_KO: Final[dict[str, str]] = {
    "노말": "normal",
    "격투": "fighting",
    "비행": "flying",
    "독": "poison",
    "땅": "ground",
    "바위": "rock",
    "벌레": "bug",
    "고스트": "ghost",
    "강철": "steel",
    "불꽃": "fire",
    "물": "water",
    "풀": "grass",
    "전기": "electric",
    "에스퍼": "psychic",
    "얼음": "ice",
    "드래곤": "dragon",
    "악": "dark",
    "페어리": "fairy",
}

CATEGORY_BY_ICON: Final[dict[str, str]] = {
    "physical": "physical",
    "special": "special",
    "status": "status",
}

CONTACT_KO: Final[str] = "접촉"
NONCONTACT_KO: Final[str] = "비접촉"

# 접촉/비접촉은 모든 기술이 정확히 하나씩 가지므로 `contact` 불리언으로 분리한다.
# 나머지는 특성 보정 그룹이다 (예리함=베기, 철주=펀치, 강한턱=물기, 방탄=구슬/폭탄).
FLAG_BY_KO: Final[dict[str, str]] = {
    "소리": "sound",
    "베기": "slicing",
    "구슬/폭탄": "bullet",
    "펀치": "punch",
    "춤": "dance",
    "바람": "wind",
    "물기": "bite",
    "가루": "powder",
    "파동": "pulse",
    # ↓ 본가에는 이 두 그룹에 **이름 붙은 플래그가 없다** (기술마다 개별 구현돼 있다).
    #   그래서 영문 키를 이 프로젝트가 정했다.
    #
    #   '압박' — 6개: 누르기 / 드래곤다이브 / 썬더다이브 / 플라잉프레스 / 헤비봄버 / 히트스탬프
    #     op.gg 설명문 3개가 명시적으로 적는다:
    #       "상대가 작아지기를 사용했다면 명중하며 데미지는 2배다"
    #     상대의 '작아지기' 회피율 상승을 무시하고 맞히며 위력이 2배가 된다.
    #     사용자 확인 완료 (2026-11-08). -> minimize_boosted
    #
    #   '상승' — 5개: 공중날기 / 튕겨오르기 / 무릎차기 / 전자부유 / 플라잉프레스
    #     ★ **기계적 공통점이 없다.** 처음에 `airborne` 이라고 이름 붙였다가 틀렸다.
    #     사용자 확인 결과 멤버가 세 갈래로 갈린다:
    #       (1) 공중날기·튕겨오르기 — 1턴차에 상승해 상대보다 빠르면 공격을 회피하고
    #           2턴차에 데미지가 들어간다. 예외 기술이 있다 (사용자 제보: 스톤엣지 등).
    #           본가의 예외 목록과 다를 수 있어 모델링 전에 확인이 필요하다.
    #       (2) 전자부유 — 5턴 동안 '부유' 특성을 얻는다. 땅 타입 기술에 맞지 않는다.
    #       (3) 무릎차기·플라잉프레스 — **효과 없음.** 설명문에 '공중'이라는 말만 있다.
    #     그래서 이름을 `rises` 로 바꿨다 — op.gg 가 '상승'으로 묶었다는 사실만 말하고
    #     기계적 성질은 주장하지 않는다. **엔진은 이 플래그로 아무 판단도 하지 않는다.**
    #     (1)(2)의 실제 성질은 플래그가 아니라 기술 단위로 따로 모델링해야 한다.
    "압박": "minimize_boosted",
    "상승": "rises",
}
# 이 프로젝트가 이름을 정한 플래그. 출처에 영문 이름이 없어 해석이 끼어든 지점이다.
NAMED_BY_US: Final[tuple[str, ...]] = ("minimize_boosted", "rises")

TARGET_BY_KO: Final[dict[str, str]] = {
    "선택한 포켓몬": "selected",
    "사용자": "self",
    "모든 상대": "all_opponents",
    "필드 전체": "entire_field",
    "다른 모든 포켓몬": "all_other_pokemon",
    "사용자 필드": "user_side",
    "무작위 상대": "random_opponent",
    "사용자 및 아군": "user_and_allies",
    "특정 기술": "specific_move",
    "상대 필드": "opponent_side",
    "사용자 또는 아군": "user_or_ally",
    "모든 포켓몬": "all_pokemon",
    "아군": "ally",
    "모든 아군": "all_allies",
    "기절한 포켓몬": "fainted_pokemon",
}

# ---------------------------------------------------------------- 불변식 범위

VALID_PP: Final[frozenset[int]] = frozenset({1, 8, 12, 16, 20})
POWER_STEP: Final[int] = 5
MIN_POWER: Final[int] = 5
MAX_POWER: Final[int] = 300
MIN_ACCURACY: Final[int] = 1
MAX_ACCURACY: Final[int] = 100
MIN_PRIORITY: Final[int] = -7
MAX_PRIORITY: Final[int] = 5
EXPECTED_MOVE_COUNT: Final[int] = 515
TABLE_COLUMNS: Final[int] = 10

# ---------------------------------------------------------------- 교차검증 화이트리스트
# champions-helper 는 출시 패치 전 스냅샷이다. 아래는 **설명 가능한** 차이이고,
# 그 근거가 밸런스 패치 기록이다. 목록 밖 차이가 생기면 둘 중 하나가 변한 것이므로
# 사람이 확인해야 한다.

EXPECTED_POWER_DIFFS: Final[dict[str, tuple[int, int]]] = {
    "apple-acid": (90, 80),
    "beak-blast": (120, 100),
    "bone-rush": (30, 25),
    "fire-lash": (90, 80),
    "first-impression": (100, 90),
    "grav-apple": (90, 80),
    "infernal-parade": (65, 60),
    "meteor-assault": (170, 150),
    "mountain-gale": (120, 100),
    "night-daze": (90, 85),
    "psyshield-bash": (90, 70),
    "slash": (80, 70),
    "snipe-shot": (85, 80),
    "spirit-shackle": (90, 80),
    "trop-kick": (85, 70),
}

EXPECTED_ACCURACY_DIFFS: Final[dict[str, tuple[int, int]]] = {
    "crabhammer": (95, 90),
    "make-it-rain": (95, 100),
    "syrup-bomb": (90, 85),
}

# champions-helper 의 오타. op.gg·smartnuo 가 '꿰매기' 로 일치한다.
EXPECTED_KO_DIFFS: Final[dict[str, tuple[str, str]]] = {
    "spirit-shackle": ("그림자꿰매기", "그림자꿰메기"),
}

# smartnuo 쪽 표기 차이. 둘 다 제3 출처로 op.gg 가 맞음을 확인했다.
#   록블라스트  — champions-helper 가 op.gg 와 같은 '록블라스트'
#   멀리짖기    — gamewith.ai/pokemon-champions/ko 가 '멀리짖기'
# 근거를 남기지 않고 화이트리스트에 넣으면 그냥 눈 감는 것이다. 반드시 근거와 함께 추가한다.
EXPECTED_KO_DIFFS_SMARTNUO: Final[dict[str, tuple[str, str]]] = {
    "rock-blast": ("록블라스트", "록블레스트"),
    "howl": ("멀리짖기", "멀리짖음"),
}

TAG_PATTERN: Final[re.Pattern[str]] = re.compile(r"<[^>]+>")
ROW_PATTERN: Final[re.Pattern[str]] = re.compile(r"<tr\b[^>]*>(.*?)</tr>", re.S)
CELL_PATTERN: Final[re.Pattern[str]] = re.compile(r"<td\b[^>]*>(.*?)</td>", re.S)
SLUG_PATTERN: Final[re.Pattern[str]] = re.compile(
    r'href="/ko/pokemon-champions/moves/([a-z0-9-]+)"'
)
CATEGORY_ICON_PATTERN: Final[re.Pattern[str]] = re.compile(
    r'src="[^"]*/(physical|special|status)\.svg'
)


def to_move_id(text: str) -> str:
    """showdown 식 id. 영숫자만 남긴 소문자.

    사용률 API 는 기술을 영문 표시명("Earthquake")으로 준다. op.gg 는 slug
    ("earthquake")로 준다. 둘을 같은 id 로 환원해야 조인이 된다.
    """
    return re.sub(r"[^a-z0-9]", "", text.lower())


def fetch_text(url: str) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=180) as response:
        raw: bytes = response.read()
    return raw.decode("utf-8", errors="replace")


def fetch_text_retrying(url: str) -> str:
    """op.gg 표는 8MB 가 넘어 전송이 끊길 여지가 가장 크다."""
    last_error: Exception | None = None
    for attempt in range(TRANSIENT_RETRIES):
        try:
            return fetch_text(url)
        except urllib.error.HTTPError:
            raise
        except (http.client.HTTPException, OSError) as error:
            last_error = error
            time.sleep(RETRY_BACKOFF_SECONDS * (attempt + 1))
    raise TransientFetchError(f"{url}: {TRANSIENT_RETRIES}회 재시도 후 실패 — {last_error}")


def fetch_json(url: str) -> Any:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=180) as response:
        return json.load(response)


class TransientFetchError(Exception):
    """재시도를 다 쓰고도 응답을 못 받았다."""


def fetch_json_retrying(url: str) -> Any:
    """전송 오류만 재시도한다. HTTP 상태 오류는 그대로 올린다.

    263회 요청 중 한 번의 응답 절단(`IncompleteRead`)으로 5분짜리 검증 전체가
    날아간 적이 있다. **데이터가 틀린 것과 네트워크가 끊긴 것은 다르다** —
    전자는 계속 시끄럽게 실패해야 하고, 후자는 재시도가 맞다.

    404 는 재시도하지 않는다. 배틀 데이터가 아직 없는 폼이라는 뜻이고, 호출자가 판단한다.
    """
    last_error: Exception | None = None
    for attempt in range(TRANSIENT_RETRIES):
        try:
            return fetch_json(url)
        except urllib.error.HTTPError:
            raise
        except (http.client.HTTPException, OSError) as error:
            last_error = error
            time.sleep(RETRY_BACKOFF_SECONDS * (attempt + 1))
    raise TransientFetchError(f"{url}: {TRANSIENT_RETRIES}회 재시도 후 실패 — {last_error}")


def cell_lines(raw_cell: str) -> list[str]:
    """셀의 표시 텍스트를 줄 단위로 끊어 돌려준다."""
    text = unescape(TAG_PATTERN.sub("\x01", raw_cell))
    return [part.strip() for part in text.split("\x01") if part.strip()]


def parse_number(text: str) -> int | None:
    """'95' / '95%' -> 95, '-' -> None."""
    stripped = text.replace("%", "").replace("+", "").strip()
    if re.fullmatch(r"-?\d+", stripped):
        return int(stripped)
    return None


def parse_opgg(html: str) -> dict[str, dict[str, Any]]:
    """op.gg 기술 표를 파싱한다.

    페이지에는 모바일 카드와 데스크톱 표가 함께 들어 있다. 데스크톱 표만
    셀이 정확히 10개라서 그걸로 구분한다. 분류(물리/특수/변화)는 텍스트가 아니라
    아이콘이므로 svg 파일명에서 읽는다.
    """
    moves: dict[str, dict[str, Any]] = {}
    for row in ROW_PATTERN.findall(html):
        slug_match = SLUG_PATTERN.search(row)
        cells = CELL_PATTERN.findall(row)
        if slug_match is None or len(cells) != TABLE_COLUMNS:
            continue

        slug = slug_match.group(1)
        move_id = to_move_id(slug)
        name_lines = cell_lines(cells[0])
        icon = CATEGORY_ICON_PATTERN.findall(cells[2])
        flag_labels = cell_lines(cells[3])
        target_labels = cell_lines(cells[8])

        moves[move_id] = {
            "slug": slug,
            "ko": name_lines[0] if name_lines else "",
            "type_ko": next(iter(cell_lines(cells[1])), ""),
            "category_icon": icon[0] if icon else "",
            "flag_labels": flag_labels,
            "power": parse_number(next(iter(cell_lines(cells[4])), "-")),
            "accuracy": parse_number(next(iter(cell_lines(cells[5])), "-")),
            "pp": parse_number(next(iter(cell_lines(cells[6])), "-")),
            "priority": parse_number(next(iter(cell_lines(cells[7])), "0")),
            "target_ko": target_labels[0] if target_labels else "",
        }
    return moves


def normalise(raw: dict[str, dict[str, Any]]) -> tuple[dict[str, dict[str, Any]], list[str]]:
    """한국어 라벨을 코드 식별자로 바꾼다. 모르는 라벨은 통과시키지 않는다 (fail-closed)."""
    errors: list[str] = []
    table: dict[str, dict[str, Any]] = {}

    for move_id, record in raw.items():
        slug = record["slug"]

        move_type = TYPE_BY_KO.get(record["type_ko"])
        if move_type is None:
            errors.append(f"{slug}: 모르는 타입 {record['type_ko']!r}")

        category = CATEGORY_BY_ICON.get(record["category_icon"])
        if category is None:
            errors.append(f"{slug}: 분류 아이콘을 못 읽었다 {record['category_icon']!r}")

        target = TARGET_BY_KO.get(record["target_ko"])
        if target is None:
            errors.append(f"{slug}: 모르는 타깃 {record['target_ko']!r}")

        labels = list(record["flag_labels"])
        contact_labels = [label for label in labels if label in (CONTACT_KO, NONCONTACT_KO)]
        if len(contact_labels) != 1:
            errors.append(f"{slug}: 접촉/비접촉이 정확히 하나여야 한다. 받은 값: {labels}")
        contact = contact_labels == [CONTACT_KO]

        flags: list[str] = []
        for label in labels:
            if label in (CONTACT_KO, NONCONTACT_KO):
                continue
            mapped = FLAG_BY_KO.get(label)
            if mapped is None:
                errors.append(f"{slug}: 모르는 플래그 {label!r}")
                continue
            flags.append(mapped)

        if move_type is None or category is None or target is None:
            continue

        table[move_id] = {
            "slug": slug,
            "type": move_type,
            "category": category,
            "power": record["power"],
            "accuracy": record["accuracy"],
            "pp": record["pp"],
            "priority": record["priority"] or 0,
            "contact": contact,
            "flags": sorted(set(flags)),
            "target": target,
        }
    return table, errors


def verify_shape(table: dict[str, dict[str, Any]], raw: dict[str, dict[str, Any]]) -> list[str]:
    """수치 불변식. 깨지면 출처의 표 구조가 변한 것이다."""
    errors: list[str] = []

    if len(table) != EXPECTED_MOVE_COUNT:
        errors.append(
            f"기술 수가 {len(table)}개다. {EXPECTED_MOVE_COUNT}개를 기대했다. "
            "패치로 기술이 추가/삭제됐다면 EXPECTED_MOVE_COUNT 를 갱신한다."
        )

    for move_id, record in sorted(table.items()):
        slug = record["slug"]

        if to_move_id(record["slug"]) != move_id:
            errors.append(f"{slug}: id 환원이 어긋난다 ({move_id})")

        power = record["power"]
        if power is not None:
            if not MIN_POWER <= power <= MAX_POWER:
                errors.append(f"{slug}: 위력 {power} 가 범위를 벗어났다")
            if power % POWER_STEP:
                errors.append(f"{slug}: 위력 {power} 가 {POWER_STEP}의 배수가 아니다")
        if record["category"] == "status" and power is not None:
            errors.append(f"{slug}: 변화 기술인데 위력 {power} 이 있다")

        accuracy = record["accuracy"]
        if accuracy is not None and not MIN_ACCURACY <= accuracy <= MAX_ACCURACY:
            errors.append(f"{slug}: 명중 {accuracy} 가 범위를 벗어났다")

        if record["pp"] not in VALID_PP:
            errors.append(
                f"{slug}: PP {record['pp']} 가 알려진 값 {sorted(VALID_PP)} 에 없다. "
                "챔피언스가 PP 체계를 바꿨을 수 있다."
            )

        if not MIN_PRIORITY <= record["priority"] <= MAX_PRIORITY:
            errors.append(f"{slug}: 우선도 {record['priority']} 가 범위를 벗어났다")

        if not raw[move_id]["ko"]:
            errors.append(f"{slug}: 한글명이 비어 있다")

    return errors


def verify_name_map(raw: dict[str, dict[str, Any]]) -> list[str]:
    """ko->en / en->ko 양방향 유일성 (§8-4)."""
    errors: list[str] = []
    by_ko: dict[str, list[str]] = {}
    for move_id, record in raw.items():
        by_ko.setdefault(record["ko"], []).append(move_id)
    for ko, ids in sorted(by_ko.items()):
        if len(ids) > 1:
            errors.append(f"한글명 {ko!r} 가 {len(ids)}개 기술에 중복된다: {sorted(ids)}")
    return errors


def cross_check_champions_helper(
    table: dict[str, dict[str, Any]], raw: dict[str, dict[str, Any]]
) -> tuple[list[str], dict[str, int]]:
    """champions-helper 와 대조한다. 설명 가능한 차이만 통과시킨다."""
    errors: list[str] = []
    payload = fetch_json_retrying(CHAMPIONS_HELPER_URL)
    other = {record["en"]: record for record in payload["moves"]}

    checked = 0
    for move_id, record in sorted(table.items()):
        slug = record["slug"]
        reference = other.get(slug)
        if reference is None:
            continue
        checked += 1

        power = record["power"]
        other_power = reference.get("pow") or None
        # 고정 위력이 없는 기술(자이로볼·카운터·앙갚음...)은 출처마다 표기가 다르다.
        # op.gg 는 '-', champions-helper 는 0 또는 1 을 쓴다. 표기법 차이지 값 충돌이
        # 아니므로 비교하지 않는다. 대신 "둘 다 고정 위력이 없다"만 확인한다.
        if power is None:
            if other_power not in (None, 0, 1):
                errors.append(
                    f"{slug}: op.gg 는 고정 위력이 없다는데 champions-helper 는 "
                    f"{other_power} 를 준다. 어느 쪽이 변했는지 확인이 필요하다."
                )
        elif power != other_power and EXPECTED_POWER_DIFFS.get(slug) != (power, other_power):
            errors.append(
                f"{slug}: 위력이 예상 밖으로 다르다 — op.gg {power} / champions-helper "
                f"{other_power}. 패치라면 EXPECTED_POWER_DIFFS 에 근거와 함께 추가한다."
            )

        accuracy = record["accuracy"]
        other_accuracy = reference.get("acc") or None
        if accuracy != other_accuracy and EXPECTED_ACCURACY_DIFFS.get(slug) != (
            accuracy,
            other_accuracy,
        ):
            errors.append(
                f"{slug}: 명중이 예상 밖으로 다르다 — op.gg {accuracy} / "
                f"champions-helper {other_accuracy}"
            )

        expected_type = record["type"].upper()
        if reference.get("type") and reference["type"] != expected_type:
            errors.append(
                f"{slug}: 타입 불일치 — op.gg {expected_type} / champions-helper "
                f"{reference['type']}"
            )

        expected_category = record["category"].upper()
        if reference.get("cat") and reference["cat"] != expected_category:
            errors.append(
                f"{slug}: 분류 불일치 — op.gg {expected_category} / champions-helper "
                f"{reference['cat']}"
            )

        ko = raw[move_id]["ko"]
        other_ko = reference.get("ko")
        if ko != other_ko and EXPECTED_KO_DIFFS.get(slug) != (ko, other_ko):
            errors.append(f"{slug}: 한글명 불일치 — op.gg {ko!r} / champions-helper {other_ko!r}")

    return errors, {"checked": checked, "source_size": len(other)}


def cross_check_smartnuo(raw: dict[str, dict[str, Any]]) -> tuple[list[str], dict[str, int]]:
    """smartnuo 와 기술 한글명을 대조한다. 수치는 제공하지 않으므로 이름만 본다."""
    errors: list[str] = []
    payload = fetch_json_retrying(SMARTNUO_URL)
    other = {record["en"]: record.get("kr") for record in payload}

    checked = 0
    for record in raw.values():
        reference = other.get(record["slug"])
        if reference is None:
            continue
        checked += 1
        slug = record["slug"]
        if record["ko"] != reference and EXPECTED_KO_DIFFS_SMARTNUO.get(slug) != (
            record["ko"],
            reference,
        ):
            errors.append(
                f"{slug}: 한글명 불일치 — op.gg {record['ko']!r} / smartnuo {reference!r}. "
                "제3 출처로 어느 쪽이 맞는지 확인한 뒤 근거와 함께 "
                "EXPECTED_KO_DIFFS_SMARTNUO 에 추가한다."
            )
    return errors, {"checked": checked, "source_size": len(other)}


def verify_usage_join(table: dict[str, dict[str, Any]]) -> tuple[list[str], dict[str, int]]:
    """사용률 API 가 참조하는 기술이 전부 이 표로 해석되는지 확인한다.

    이게 깨지면 Phase 2-5 에서 채용률을 기술에 붙일 수 없다. 조인이 안 되는 기술을
    조용히 건너뛰면 "이 기술 채용률 0%" 라는 틀린 화면이 나온다 (§8-4 fail-closed).
    """
    errors: list[str] = []
    index = fetch_json_retrying(BATTLE_DATA_INDEX)
    showdown_ids = [entry["showdownId"] for entry in index["pokemon"]]

    seen: dict[str, str] = {}
    fetched = 0
    skipped_404 = 0
    for position, showdown_id in enumerate(showdown_ids):
        try:
            payload = fetch_json_retrying(BATTLE_DATA_ROW.format(showdown_id=showdown_id))
        except urllib.error.HTTPError as error:
            if error.code == 404:  # 배틀 데이터가 아직 없는 폼
                skipped_404 += 1
                continue
            errors.append(f"{showdown_id}: 배틀 데이터 조회 실패 {error}")
            continue
        except TransientFetchError as error:
            # 조용히 넘기지 않는다. 한 종을 못 읽으면 "전수 조인했다"고 말할 수 없다.
            errors.append(f"{showdown_id}: {error}")
            continue
        finally:
            if position < len(showdown_ids) - 1:
                time.sleep(REQUEST_DELAY_SECONDS)
        fetched += 1
        for row in payload.get("rows", []):
            if row.get("category") == "move" and row.get("name"):
                seen[to_move_id(str(row["name"]))] = str(row["name"])

    missing = sorted(name for move_id, name in seen.items() if move_id not in table)
    for name in missing:
        errors.append(f"사용률에 쓰인 기술 {name!r} 를 기술표에서 찾을 수 없다")

    return errors, {
        "pokemon_fetched": fetched,
        "pokemon_without_battle_data": skipped_404,
        "moves_seen": len(seen),
    }


def write_files(
    table: dict[str, dict[str, Any]],
    raw: dict[str, dict[str, Any]],
    checks: dict[str, Any],
) -> None:
    moves_payload = {
        "_meta": {
            "source": OPGG_URL,
            "generated_by": "backend/scripts/generate_moves.py",
            "key": (
                "showdown 식 기술 id (영숫자만 남긴 소문자). 사용률 API 의 영문 표시명과 조인한다."
            ),
            "note": (
                "챔피언스 기술 수치는 본가와 다르다. 밸런스 패치로 위력·명중이 조정되고 "
                "PP 는 {1,8,12,16,20} 로 정규화돼 있다. 본가 데이터로 대체하면 안 된다 (ADR-009). "
                "위력 null = 고정 위력 없음(자이로볼·지구던지기처럼 상황에 따라 결정). "
                "명중 null = 명중 판정 없음. 한글명은 name_map_moves.json 소관 (ADR-005)."
            ),
            "flags_named_by_us": {
                "minimize_boosted": {
                    "opgg_label": "압박",
                    "members": 6,
                    "meaning": (
                        "상대가 '작아지기'로 회피율을 올린 상태를 무시하고 명중하며 "
                        "위력이 2배가 되는 기술 그룹."
                    ),
                    "evidence": (
                        'op.gg 설명문에 "상대가 작아지기를 사용했다면 명중하며 '
                        '데미지는 2배다"가 명시돼 있다.'
                    ),
                    "confirmed_by_user": True,
                    "usable_as_a_group": True,
                },
                "rises": {
                    "opgg_label": "상승",
                    "members": 5,
                    "meaning": (
                        "op.gg 가 '상승'으로 묶었다는 사실만 나타낸다. 기계적 공통점은 없다."
                    ),
                    "evidence": (
                        "사용자 확인(2026-11-08) 결과 멤버가 세 갈래로 갈린다. "
                        "(1) 공중날기·튕겨오르기 = 1턴차 상승으로 회피 후 2턴차 데미지, "
                        "예외 기술 있음(미확인). "
                        "(2) 전자부유 = 5턴간 '부유' 특성, 땅 타입 무효. "
                        "(3) 무릎차기·플라잉프레스 = 효과 없음(설명문 표현만)."
                    ),
                    "confirmed_by_user": True,
                    "usable_as_a_group": False,
                },
                "note": (
                    "본가에는 이 두 그룹에 이름 붙은 플래그가 없어 영문 키를 이 프로젝트가 "
                    "정했다. 해석이 끼어든 지점이므로 근거를 함께 남긴다. "
                    "usable_as_a_group=false 인 플래그로는 엔진이 아무 판단도 하지 않는다."
                ),
            },
            "cross_checks": checks,
            "count": len(table),
        },
        "moves": dict(sorted(table.items())),
    }

    name_map_payload = {
        "_meta": {
            "sources": [OPGG_URL, CHAMPIONS_HELPER_URL, SMARTNUO_URL],
            "generated_by": "backend/scripts/generate_moves.py",
            "key": "showdown 식 기술 id -> 한국 서버 명칭",
            "note": (
                "Phase 2-3 한영 매핑의 기술 부분. 세 출처를 대조해 생성했다. "
                "퍼지 매칭 금지 — 정확 일치 조회만 한다 (§8-4)."
            ),
            "count": len(raw),
        },
        "moves": {
            move_id: {"ko": raw[move_id]["ko"], "slug": raw[move_id]["slug"]}
            for move_id in sorted(raw)
        },
    }

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    for path, payload in ((MOVES_PATH, moves_payload), (NAME_MAP_PATH, name_map_payload)):
        with path.open("w", encoding="utf-8", newline="\n") as file:
            json.dump(payload, file, ensure_ascii=False, indent=2)
            file.write("\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="챔피언스 기술 마스터 시드 생성")
    parser.add_argument(
        "--skip-usage-join",
        action="store_true",
        help="사용률 API 전수 조인 검증을 건너뛴다 (263회 요청, 약 5분)",
    )
    args = parser.parse_args()

    print(f"op.gg 기술 표 수집: {OPGG_URL}")
    raw = parse_opgg(fetch_text_retrying(OPGG_URL))
    print(f"  파싱 {len(raw)}개")

    table, errors = normalise(raw)
    errors += verify_shape(table, raw)
    errors += verify_name_map(raw)

    checks: dict[str, Any] = {}

    print("champions-helper 대조...")
    helper_errors, helper_stats = cross_check_champions_helper(table, raw)
    errors += helper_errors
    checks["champions_helper"] = {
        "url": CHAMPIONS_HELPER_URL,
        **helper_stats,
        "expected_power_diffs": len(EXPECTED_POWER_DIFFS),
        "expected_accuracy_diffs": len(EXPECTED_ACCURACY_DIFFS),
    }
    print(f"  공통 {helper_stats['checked']}개 대조")

    time.sleep(REQUEST_DELAY_SECONDS)
    print("smartnuo 대조...")
    nuo_errors, nuo_stats = cross_check_smartnuo(raw)
    errors += nuo_errors
    checks["smartnuo"] = {"url": SMARTNUO_URL, **nuo_stats}
    print(f"  공통 {nuo_stats['checked']}개 대조")

    if args.skip_usage_join:
        print("사용률 조인 검증 생략 (--skip-usage-join)")
        checks["usage_join"] = {"skipped": True}
    else:
        print("사용률 API 전수 조인 검증 (263종, 요청 간 1초)...")
        join_errors, join_stats = verify_usage_join(table)
        errors += join_errors
        checks["usage_join"] = {"source": BATTLE_DATA_INDEX, **join_stats, "unresolved": 0}
        print(
            f"  포켓몬 {join_stats['pokemon_fetched']}종 / 기술 {join_stats['moves_seen']}개 해석"
        )

    if errors:
        print(f"\n검증 실패 {len(errors)}건 — 파일을 쓰지 않는다.", file=sys.stderr)
        for line in errors[:40]:
            print(f"  {line}", file=sys.stderr)
        raise SystemExit(1)

    write_files(table, raw, checks)
    print(f"\n검증 통과. 기술 {len(table)}개")
    print(f"  {MOVES_PATH}")
    print(f"  {NAME_MAP_PATH}")


if __name__ == "__main__":
    main()
