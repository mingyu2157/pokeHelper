"""수집 원본 행 -> 내부 스키마 (CLAUDE.md 8-6).

**사이트별 필드명을 DB 에 그대로 넣지 않는다.** `stat_alignment`, `percentage_value`,
`hp_points` 같은 championsbattledata 의 어휘가 이 모듈 밖으로 나가지 않는다.
나가게 두면 두 번째 출처를 붙일 때 DB·서비스까지 전부 고쳐야 한다.

★ 노력치 배분은 여기서 검증한다 ★

챔피언스는 스탯당 0~32, 총합 66 이하다 (CLAUDE.md 5-0). 출처가 불가능한 배분을 주면
그대로 저장하지 않고 걸러낸다 — 불가능한 배분으로 계산한 수치를 사용자에게 보여주는 건
계산 오류보다 나쁘다. 검증은 `domain/effort.py` 의 규칙을 그대로 쓴다.
"""

from __future__ import annotations

import re
from typing import Any, Final

from app.domain.constants import MAX_POINTS_PER_STAT, MAX_TOTAL_POINTS
from app.ingestion.base import OptionUsage, SpreadUsage

# 출처의 category -> 내부 이름
CATEGORY_FIELD: Final[dict[str, str]] = {
    "move": "moves",
    "held_item": "items",
    "ability": "abilities",
    "stat_alignment": "natures",
    "teammate": "teammates",
}

# 출처의 포인트 컬럼 -> 내부 스탯 키 (HABCDS 순서)
POINT_FIELDS: Final[tuple[tuple[str, str], ...]] = (
    ("hp_points", "hp"),
    ("attack_points", "attack"),
    ("defense_points", "defense"),
    ("sp_atk_points", "sp_attack"),
    ("sp_def_points", "sp_defense"),
    ("speed_points", "speed"),
)

SPREAD_CATEGORY: Final[str] = "stat_points"


class NormalizationError(Exception):
    """원본이 우리 가정과 다르다. 스냅샷을 활성화하지 않는다."""


def to_id(text: str) -> str:
    """showdown 식 id. 영숫자만 남긴 소문자.

    사용률 API 는 영문 표시명("Rough Skin")을 주고, 우리 마스터는 id("roughskin")로
    조인한다. 기술·특성·성격·포켓몬 전부 같은 규칙이다.
    """
    return re.sub(r"[^a-z0-9]", "", text.lower())


def _rate(row: dict[str, Any]) -> float | None:
    """채용률. 없으면 None 이다 — 0.0 으로 채우지 않는다.

    파트너(teammate)는 순위만 주고 비율을 주지 않는다. 거기에 0 을 넣으면
    "아무도 안 쓴다"는 거짓이 된다.
    """
    value = row.get("percentage_value")
    if value is None:
        return None
    return float(value)


def _points(row: dict[str, Any]) -> dict[str, int]:
    points: dict[str, int] = {}
    for source_field, stat_key in POINT_FIELDS:
        raw = row.get(source_field)
        if raw is None or raw == "":
            points[stat_key] = 0
            continue
        try:
            points[stat_key] = int(raw)
        except (TypeError, ValueError) as error:
            raise NormalizationError(
                f"노력치 포인트가 정수가 아니다: {source_field}={raw!r}"
            ) from error
    return points


def _validate_points(points: dict[str, int]) -> None:
    """챔피언스 규칙. 위반하면 저장하지 않는다 (CLAUDE.md 5-0)."""
    for stat_key, value in points.items():
        if not 0 <= value <= MAX_POINTS_PER_STAT:
            raise NormalizationError(
                f"노력치가 스탯당 0~{MAX_POINTS_PER_STAT} 범위를 벗어났다: {stat_key}={value}"
            )
    total = sum(points.values())
    if total > MAX_TOTAL_POINTS:
        raise NormalizationError(f"노력치 총합이 {MAX_TOTAL_POINTS} 를 넘었다: {total} ({points})")


def normalize_rows(rows: list[dict[str, Any]]) -> dict[str, tuple[Any, ...]]:
    """배틀 데이터 행들을 카테고리별 내부 타입으로 바꾼다.

    모르는 카테고리는 **조용히 버리지 않고** 실패시킨다. 출처가 새 카테고리를
    추가했는데 우리가 모르면, 그걸 빠뜨린 채로 "수집 성공"이라고 말하게 된다.
    """
    buckets: dict[str, list[OptionUsage]] = {field: [] for field in CATEGORY_FIELD.values()}
    spreads: list[SpreadUsage] = []

    for row in rows:
        category = str(row.get("category") or "")
        rank_raw = row.get("rank")
        if rank_raw is None:
            raise NormalizationError(f"rank 가 없는 행이다: {row}")
        rank = int(rank_raw)

        if category == SPREAD_CATEGORY:
            points = _points(row)
            _validate_points(points)
            spreads.append(SpreadUsage(points=points, rank=rank, rate=_rate(row)))
            continue

        field = CATEGORY_FIELD.get(category)
        if field is None:
            raise NormalizationError(
                f"모르는 카테고리 {category!r}. 출처가 항목을 추가했을 수 있다 — "
                "빠뜨린 채로 수집 성공이라고 말하지 않는다."
            )

        name = str(row.get("name") or "").strip()
        if not name:
            raise NormalizationError(f"이름이 비어 있는 {category} 행이다: {row}")

        buckets[field].append(OptionUsage(option_id=to_id(name), rank=rank, rate=_rate(row)))

    result: dict[str, tuple[Any, ...]] = {
        field: tuple(sorted(items, key=lambda item: item.rank)) for field, items in buckets.items()
    }
    result["spreads"] = tuple(sorted(spreads, key=lambda item: item.rank))
    return result
