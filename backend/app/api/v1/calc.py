"""계산 엔드포인트. 라우터는 얇게 유지한다 (CLAUDE.md 2장)."""

from fastapi import APIRouter, Depends

from app.api.deps import RateLimit
from app.schemas.ev_tune import EvTuneInfeasible, EvTuneRequest, EvTuneSolution
from app.services import ev_tune

router = APIRouter(prefix="/api/v1/calc", tags=["calc"])


@router.post(
    "/ev-tune",
    # ★ 계산 엔드포인트는 읽기보다 빡세게 센다 ★ 제약 8개에서 96ms 를 쓴다.
    # 읽기와 같은 한도를 주면 CPU 를 지키지 못한다 (ADR-019).
    dependencies=[Depends(RateLimit("calc"))],
    response_model=EvTuneSolution | EvTuneInfeasible,
    summary="노력치 미세조정",
    description=(
        "제약조건을 만족하는 노력치 배분을 역산한다. "
        "해가 없으면 `feasible: false` 와 함께 무엇이 충돌하는지, "
        "무엇을 포기하면 되는지를 돌려준다. 오류가 아니라 유효한 결과다."
    ),
)
def ev_tune_endpoint(request: EvTuneRequest) -> EvTuneSolution | EvTuneInfeasible:
    return ev_tune.tune(request)
