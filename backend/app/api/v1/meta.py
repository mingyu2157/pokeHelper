"""메타 현황 엔드포인트. 라우터는 얇게 유지한다 (CLAUDE.md 2장).

★ 이 API 는 출처의 덤프가 아니다 (CLAUDE.md 8-2) ★

라이선스가 "경쟁 API·덤프·대량 다운로드 서비스"를 금지한다. 그래서
설계에 세 가지가 박혀 있다.

1. `limit` 에 **상한**이 있다 (`MAX_ENTRY_LIMIT`). 전체를 주는 경로가 없다.
2. 수집 원본(`pokemon_meta.raw`)은 저장소가 **조회조차 하지 않는다.**
3. 모든 응답에 출처 표기가 **필수 필드**로 함께 나간다.

세 번째가 핵심이다 — 우리 API 를 쓰는 쪽이 출처를 모를 수가 없다.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.schemas.meta import (
    DEFAULT_ENTRY_LIMIT,
    MAX_ENTRY_LIMIT,
    BattleFormat,
    PokemonMetaOut,
    UsageListOut,
)
from app.services import meta
from app.services.exceptions import NotAvailableError

router = APIRouter(prefix="/api/v1/meta", tags=["meta"])

# 의존성·쿼리 선언을 Annotated 로 둔다. 기본값 자리에 함수 호출을 두면
# ruff(B008)가 막는다 — 그 규칙이 옳다. 기본값은 모듈 로드 시 한 번만
# 평가되므로, 호출처럼 보이는 것이 매 요청 호출되지 않는다는 착각을 부른다.
SessionDep = Annotated[AsyncSession, Depends(get_session)]
FormatQuery = Annotated[
    BattleFormat,
    Query(alias="format", description="대전 포맷. 이 서비스는 싱글 기준이다 (CLAUDE.md 8-1)."),
]
LimitQuery = Annotated[
    int,
    Query(
        ge=1,
        le=MAX_ENTRY_LIMIT,
        description=f"돌려줄 엔트리 수. 상한 {MAX_ENTRY_LIMIT} — 덤프 방지 장치다.",
    ),
]


@router.get(
    "/usage",
    response_model=UsageListOut,
    summary="사용 순위표",
    description=(
        "활성 스냅샷의 포켓몬 사용 순위. **사용률 퍼센트는 출처가 주지 않으므로 "
        "`usage_rate` 는 거의 항상 null 이고 0 으로 대체하지 않는다.** "
        "모든 응답에 출처 표기와 수집 시각이 함께 나간다."
    ),
)
async def usage_endpoint(
    session: SessionDep,
    battle_format: FormatQuery = "Singles",
    limit: LimitQuery = DEFAULT_ENTRY_LIMIT,
) -> UsageListOut:
    try:
        return await meta.usage_list(session, battle_format=battle_format, limit=limit)
    except NotAvailableError as exc:
        # 404 다. 서버 오류가 아니라 "아직 수집된 메타가 없다"는 상태다.
        # 개별 원인이 아니라 **계층 베이스**를 잡는다 — 돌려줄 게 없는 이유가
        # 늘어나도 라우터는 그대로다 (CLAUDE.md 6장).
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.get(
    "/pokemon/{pokemon_id}",
    response_model=PokemonMetaOut,
    summary="포켓몬 채용률 상세",
    description=(
        "기술·도구·특성·성격·파트너 채용률과 노력치 배분(HABCDS 표기). "
        "한글명 매핑이 없는 선택지는 `name_ko` 가 null 이고 `unmapped` 에 개수가 잡힌다 — "
        "영문 id 를 한글명 자리에 넣지 않는다."
    ),
)
async def pokemon_endpoint(
    pokemon_id: str,
    session: SessionDep,
    battle_format: FormatQuery = "Singles",
) -> PokemonMetaOut:
    try:
        return await meta.pokemon_detail(
            session, battle_format=battle_format, pokemon_id=pokemon_id
        )
    except NotAvailableError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
