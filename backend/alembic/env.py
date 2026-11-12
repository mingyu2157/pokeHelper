"""Alembic 환경 설정 (async).

★ DB 접속 정보를 alembic.ini 에 적지 않는다 ★

`app.core.config` 가 환경변수를 읽는 유일한 자리다 (CLAUDE.md). ini 에 URL 을 박으면
개발/운영에서 서로 다른 값이 필요할 때 파일을 갈아끼우게 되고, 비밀번호가 커밋된다.

`target_metadata` 는 `app.db.models` 의 것을 쓴다. 모델을 import 해야 테이블이
메타데이터에 등록되므로, 모델을 추가하면 그 모듈이 import 되는지 확인한다.
"""

from __future__ import annotations

import asyncio
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context
from app.core.config import get_settings
from app.db import models as _models  # noqa: F401 - 테이블 등록을 위해 import 한다
from app.db.base import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# 환경변수에서 읽어 주입한다. ini 의 sqlalchemy.url 은 비워 둔다.
config.set_main_option("sqlalchemy.url", get_settings().database_url)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """DB 연결 없이 SQL 만 생성한다."""
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        # 컬럼 타입 변경도 자동 감지한다. 없으면 SmallInteger -> Integer 같은 변경을
        # 조용히 놓친다.
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
