"""도메인 예외를 HTTP 응답으로 옮긴다.

도메인 예외 자체는 `app/domain/exceptions.py` 에 있다 (ADR-007).
여기는 그 반대 방향 — 계층 의존은 `api → domain` 한 방향으로만 흐른다.

불가능한 입력은 **422** 다. 서버가 고장난 게 아니라 요청이 성립하지 않는다.
"""

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse

from app.domain.exceptions import DomainError


class ConfigError(Exception):
    """설정 계층 오류의 최상위.

    ★ 계층 베이스가 넷이 된 경위 ★
    예외 계층 규칙을 만들 때는 도메인·수집·서비스 셋뿐이었다. 배포 선결
    조건을 넣으면서 **설정 검증**이라는 네 번째 자리가 생겼다 — 도메인
    규칙 위반도, 수집 실패도, "돌려줄 게 없음"도 아니다.
    규칙을 어기는 대신 규칙을 늘렸고, 테스트가 그대로 집행한다.
    """


async def domain_error_handler(request: Request, exc: Exception) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        content={"detail": str(exc), "error": type(exc).__name__},
    )


def register_handlers(app: FastAPI) -> None:
    app.add_exception_handler(DomainError, domain_error_handler)
