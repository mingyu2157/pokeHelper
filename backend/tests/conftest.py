"""전역 테스트 설정.

★ 속도 제한 카운터를 테스트마다 비운다 (ADR-019) ★

제한을 붙이자 기존 통합 테스트 31개가 429 로 깨졌다. 테스트들이 같은
엔드포인트를 반복해서 때리는데, `TestClient` 는 전부 같은 클라이언트로
보이기 때문에 **한 모듈의 요청이 전부 한 바구니에 쌓였다.**

제한을 **끄지 않는다.** 끄면 라우터에 의존성이 붙어 있는지조차 테스트가
보지 못하고, "정의했지만 적용되지 않은" 상태를 놓친다 — 이 프로젝트가
반복해서 막아온 종류의 실패다 (출처 표기·DB 권한·경보 지표명).

대신 테스트마다 카운터를 비운다. 테스트 하나 = 클라이언트 하나로 보는
것이고, 실제 동작과도 맞다. 한도 자체는
`tests/integration/test_deploy_prereqs.py` 가 따로 단정한다.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _reset_rate_limit_counters():
    """앞 테스트가 쓴 횟수가 다음 테스트로 넘어가지 않게 한다."""
    from app.api import deps

    deps.reset_limiter_store()
    yield
    deps.reset_limiter_store()
