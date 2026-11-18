"""FastAPI 애플리케이션 진입점.

라우터는 얇게 유지한다. 계산 로직은 app/domain/, 유스케이스 조합은 app/services/ 에 둔다.
"""

from fastapi import Depends, FastAPI, Response
from fastapi.middleware.cors import CORSMiddleware
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from prometheus_fastapi_instrumentator import Instrumentator
from prometheus_fastapi_instrumentator import metrics as instrumentator_metrics

from app.api.deps import require_metrics_token
from app.api.v1.calc import router as calc_router
from app.api.v1.health import router as health_router
from app.api.v1.meta import router as meta_router
from app.core.config import assert_production_ready, get_settings
from app.core.exceptions import register_handlers
from app.services import observability

settings = get_settings()

# ★ 운영으로 뜨려는데 열려 있으면 기동을 거부한다 (ADR-019) ★
# 경고 로그로 끝내면 아무도 안 본다. 배포 파이프라인이 멈춰야 사람이 본다.
# 열린 채로 서비스되는 것보다 안 뜨는 게 낫다.
assert_production_ready(settings)

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

# 도메인 예외를 422 로 옮긴다. 불가능한 입력은 서버 오류가 아니다.
register_handlers(app)

# /health 는 로드밸런서가 때리는 경로라 버전 프리픽스 없이 루트에 둔다.
app.include_router(health_router)
app.include_router(calc_router)
app.include_router(meta_router)

# ── 관측 지표 (ADR-016) ──────────────────────────────────────────────
#
# instrument() 만 호출하고 expose() 는 쓰지 않는다. /metrics 를 직접
# 정의해야 스크랩 때 ETL 신선도를 함께 갱신할 수 있다.
#
# ★ /metrics 를 자기 자신의 지표에서 제외한다 ★ 포함하면 스크랩 간격이
#   요청률 그래프에 상수로 깔려서, 실제 사용자 트래픽이 묻힌다.
#
# ★ 이 경로는 외부에 노출하지 않는다 ★ 경로별 지연·오류율은 내부 정보다.
#   지금은 로컬 compose 전용이고, 배포 시 ALB 수준에서 막는다 (ADR-016).
# ★ 버킷을 실측에서 정한다 ★
# 라이브러리 기본 경로별 버킷은 (0.1, 0.5, 1.0) 셋뿐이다. 그런데 노력치 솔버는
# 제약 8개에서 2.2초가 걸린다 (2026-11-17 실측, JOURNAL 참조). 기본값을 쓰면
# **2.2초가 전부 +Inf 버킷에 들어가서** 1.1초와 60초를 구분할 수 없고,
# histogram_quantile 이 경로별로는 쓸모가 없어진다. 정작 느린 엔드포인트 하나를
# 못 보는 대시보드가 된다.
#
# 측정값: /health 2.6ms · 제약1 16ms · 제약4 56ms · 제약7 442ms · 제약8 2,215ms
# 그 분포에 해상도를 맞춘다. 상한 5초는 "이건 분명히 비정상" 지점이다.
LATENCY_BUCKETS = (0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 3.0, 5.0)

Instrumentator(excluded_handlers=["/metrics"]).add(
    instrumentator_metrics.default(latency_lowr_buckets=LATENCY_BUCKETS)
).instrument(app)


@app.get(
    "/metrics",
    include_in_schema=False,
    dependencies=[Depends(require_metrics_token)],
)
async def metrics() -> Response:
    await observability.refresh_if_stale()
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
