"""헬스 체크 라우터.

Phase 0 시점에서는 프로세스 생존 여부만 본다(liveness).
DB/Redis 연결까지 확인하는 readiness 는 세션 계층이 생기는 Phase 2 에서 추가한다.
"""

from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter()


class HealthResponse(BaseModel):
    status: Literal["ok"]
    service: str
    version: str


@router.get("/health", response_model=HealthResponse, tags=["health"])
async def health() -> HealthResponse:
    return HealthResponse(status="ok", service="pokehelper-backend", version="0.1.0")
