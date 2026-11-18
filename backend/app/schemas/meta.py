"""메타 현황 응답 스키마.

★ 이 파일이 라이선스를 집행하는 자리다 (CLAUDE.md 8-2, ADR-013) ★

`attributions` 에 **기본값을 두지 않는다.** 기본값이 있으면 출처 표기를
빠뜨린 응답을 만들어도 코드가 돌아가고, 화면에는 숫자가 멀쩡히 나오면서
출처 줄만 비어 있다. 눈에 띄지 않는 위반이라 타입이 막아야 한다.
수집 쪽 `CollectionResult.attribution` 과 **같은 장치**다.

★ `raw`(수집 원본)는 어떤 스키마에도 없다 ★

우리 API 는 **계산 결과와 분석**을 돌려주고, 원본 통계는 UI 표시에 필요한
만큼만 싣는다. 원본을 그대로 돌려주는 엔드포인트는 라이선스가 금지하는
"경쟁 API·덤프"가 된다.
"""

from __future__ import annotations

from datetime import datetime
from typing import Final, Literal

from pydantic import BaseModel, Field

#: 한 번에 돌려줄 수 있는 최대 엔트리 수.
#:
#: ★ 상한이 있다는 것 자체가 설계다 ★ 상한이 없으면 이 엔드포인트가 활성
#: 스냅샷 전체를 한 번에 주는 덤프가 된다. 화면이 실제로 쓰는 것은 상위
#: 수십 줄이고, 그보다 많이 필요한 쪽은 사용자가 아니라 수집기다.
MAX_ENTRY_LIMIT: Final[int] = 60
DEFAULT_ENTRY_LIMIT: Final[int] = 30

#: 선택지 카테고리. 출처가 주는 것과 1:1 이다.
OptionCategory = Literal["moves", "items", "abilities", "natures", "teammates"]

BattleFormat = Literal["Singles", "Doubles"]


class AttributionOut(BaseModel):
    """표시 의무가 있는 출처 표기. 문구와 링크는 **쌍으로** 나간다."""

    text: str = Field(description="화면에 그대로 띄우는 문구. 임의로 고치지 않는다.")
    url: str = Field(description="출처 링크. 표기와 링크 둘 다가 라이선스 조건이다.")


class SnapshotOut(BaseModel):
    """이 응답이 어느 수집분에서 나왔는가.

    수집 시각을 **같이 내보내는 것이 필수다** (CLAUDE.md 1-2). 통계는 언제
    수집했는지에 따라 의미가 달라지는데, 화면에 숫자만 있으면 사용자는 그게
    오늘 것인지 2주 전 것인지 알 수 없다.
    """

    source: str
    format: BattleFormat
    regulation: str = Field(description="레귤레이션 코드 (예: M7)")
    collected_at: datetime


class UsageEntryOut(BaseModel):
    """순위표 한 줄.

    ★ `usage_rate` 는 거의 항상 null 이다 ★ 출처 API 가 순위만 주고 사용률
    퍼센트를 주지 않는다. **0 으로 채우지 않는다** — "사용률 0%"와 "사용률을
    모른다"는 다르다. 화면은 순위를 보여준다.
    """

    pokemon_id: str = Field(description="Showdown 내부 ID. 상세 조회의 키다.")
    name_ko: str | None = Field(
        description="한국 서버 명칭. 매핑이 없으면 null 이고, 화면은 이를 미매핑으로 표시한다."
    )
    rank: int | None
    rank_change: int | None = Field(
        description="직전 스냅샷 대비 순위 변동. 양수가 상승이다. 비교 대상이 없으면 null."
    )
    usage_rate: float | None = Field(
        description="출처가 주지 않는다. 거의 항상 null 이며 0 으로 대체하지 않는다."
    )


class UsageListOut(BaseModel):
    """순위표 응답."""

    snapshot: SnapshotOut
    attributions: list[AttributionOut] = Field(
        description="이 응답에 들어간 데이터의 출처 전부. 기본값 없음 — 라이선스 조건이다."
    )
    entries: list[UsageEntryOut]


class MetaOptionOut(BaseModel):
    """선택지 하나의 채용률.

    `rate` 가 null 인 경우가 있다 — 파트너는 순위만 주고 비율을 주지 않는다.
    0.0 으로 채우지 않는다.
    """

    option_id: str
    name_ko: str | None = Field(
        description="한글명. **매핑이 없으면 null 이다.** 영문 id 를 한글명 자리에 넣지 않는다."
    )
    rank: int
    rate: float | None


class MetaSpreadOut(BaseModel):
    """노력치 배분 채용률.

    ★ 챔피언스 표기다 (CLAUDE.md 5-0) ★ 스탯당 0~32, 총합 66 이하.
    기존 시리즈의 노력치 표기는 이 프로젝트에 존재하지 않는다.

    (이 독스트링은 OpenAPI 설명으로 나간다. 기존 표기의 숫자를 **부정하는
    문장으로도** 적지 않는다 — 계약을 훑는 가드 테스트는 문맥을 모른다.)
    """

    notation: str = Field(description="UI 표준 HABCDS 표기 (예: A32S32H2)")
    points: dict[str, int]
    rank: int
    rate: float | None


class PokemonMetaOut(BaseModel):
    """포켓몬 하나의 채용률 상세."""

    snapshot: SnapshotOut
    attributions: list[AttributionOut]
    pokemon_id: str
    name_ko: str | None
    rank: int | None
    rank_change: int | None
    options: dict[OptionCategory, list[MetaOptionOut]]
    spreads: list[MetaSpreadOut]
    unmapped: dict[OptionCategory, int] = Field(
        description=(
            "카테고리별 한글명 미매핑 개수. 0 이 아니면 화면이 그 사실을 표시해야 한다. "
            "숨기면 사용자는 영문 id 를 우리가 고른 표기로 오해한다."
        )
    )
