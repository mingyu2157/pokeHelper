"""FastAPI 애플리케이션 진입점.

라우터는 얇게 유지한다. 계산 로직은 app/domain/, 유스케이스 조합은 app/services/ 에 둔다.
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.health import router as health_router
from app.core.config import get_settings

settings = get_settings()

app = FastAPI(
    title="pokeHelper API",
    description="포켓몬 챔피언스 파티 구축 + 선출 판단 보조 API",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# /health 는 로드밸런서가 때리는 경로라 버전 프리픽스 없이 루트에 둔다.
app.include_router(health_router)
