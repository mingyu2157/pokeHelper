"""비동기 DB 세션.

`api -> services -> repositories -> db` 방향의 끝이다. domain 은 여기를 모른다.

엔진은 프로세스당 하나다. 요청마다 만들면 커넥션 풀이 매번 새로 생겨서
Postgres 의 연결 수를 금방 소진한다.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from functools import lru_cache

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import get_settings


@lru_cache(maxsize=1)
def get_engine() -> AsyncEngine:
    settings = get_settings()
    return create_async_engine(
        settings.database_url,
        # 커넥션이 끊긴 채로 풀에 남아 있는 경우를 걸러낸다. 컨테이너를 재시작하면
        # 실제로 발생하고, 없으면 첫 쿼리가 알 수 없는 이유로 실패한다.
        pool_pre_ping=True,
        echo=False,
    )


@lru_cache(maxsize=1)
def get_session_factory() -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(
        bind=get_engine(),
        expire_on_commit=False,  # 커밋 후에도 객체를 읽을 수 있어야 응답 직렬화가 된다
        autoflush=False,
    )


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI 의존성. 요청당 세션 하나를 열고 끝나면 닫는다."""
    factory = get_session_factory()
    async with factory() as session:
        yield session
