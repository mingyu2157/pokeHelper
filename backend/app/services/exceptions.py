"""서비스 계층 예외.

유스케이스가 "요청은 유효한데 **지금 돌려줄 것이 없다**"를 알리는 자리다.
도메인 규칙 위반(`DomainError`, 422)과는 다른 범주다 — 입력이 틀린 게 아니라
**상태**가 그렇다.

계층마다 예외를 자급하는 이유는 `ingestion/exceptions.py` 머리말과 같다.
"""

from __future__ import annotations


class ServiceError(Exception):
    """서비스 계층 오류의 최상위."""


class NotAvailableError(ServiceError):
    """요청은 유효하지만 돌려줄 데이터가 없다. API 에서 404 가 된다.

    ★ 오류가 아니라 상태다 ★ 수집을 한 번도 안 했거나, 검증 실패로 활성
    전환이 막혀 있을 수 있다. 어느 쪽이든 계산 기능은 영향받지 않는다
    (CLAUDE.md 8-5).
    """


class SnapshotNotFoundError(NotAvailableError):
    """해당 포맷의 활성 스냅샷이 없다."""


class PokemonNotInSnapshotError(NotAvailableError):
    """그 포켓몬이 활성 스냅샷에 없다. 순위권 밖이거나 id 가 틀렸다."""
