"""SQLAlchemy 선언 기반 클래스.

**domain 계층은 이 모듈을 import 하지 않는다** (CLAUDE.md 1-5). 의존 방향은
`api -> services -> repositories -> db` 한 방향이다.
"""

from __future__ import annotations

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """모든 테이블의 공통 조상."""
