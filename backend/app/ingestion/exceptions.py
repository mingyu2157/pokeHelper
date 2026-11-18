"""수집 계층 예외.

★ 왜 `core/exceptions.py` 가 아니라 여기인가 ★

CLAUDE.md 1-5 가 "`app/domain/` 은 아무것도 import 하지 않는다"고 못 박는다.
그래서 도메인 예외는 구조적으로 `core/` 에 둘 수 없고, 실제로
`domain/exceptions.py` 에 `DomainError` 로 자급한다. 계층마다 예외를 자급하는
것이 이 프로젝트의 실제 규칙이고, 수집 계층도 같은 모양을 따른다.

(`core/exceptions.py` 는 카탈로그가 아니라 **도메인 예외를 HTTP 로 옮기는
핸들러 등록 모듈**이다. 이름이 비슷해서 헷갈리기 쉽다.)

★ 베이스가 있는 이유 ★

맨 `Exception` 을 상속하면 "수집 중 난 오류 전부"를 한 번에 잡을 수 없다.
수집 CLI 와 스케줄러는 **개별 실패 원인이 아니라 "수집이 실패했다"** 로
반응해야 한다 (종료 코드 1, 활성 스냅샷 유지). 그 경계를 타입으로 둔다.
"""

from __future__ import annotations


class IngestionError(Exception):
    """수집 계층 오류의 최상위.

    이걸 잡으면 "수집이 실패했다"를 한 번에 처리할 수 있다. 개별 원인을
    구분해야 하는 곳만 하위 타입을 잡는다.
    """


class CollectionError(IngestionError):
    """수집이 실패했다. 스냅샷을 활성화하지 않는다.

    네트워크·HTTP·응답 형식처럼 **출처 쪽** 문제다.
    """


class NormalizationError(IngestionError):
    """원본이 우리 가정과 다르다. 스냅샷을 활성화하지 않는다.

    받기는 받았는데 **우리가 읽을 수 있는 모양이 아니다.** 출처가 스키마를
    바꾼 경우가 여기다.
    """


class UnknownSourceError(IngestionError):
    """출처 표기를 모르는 출처다. 표기 없이 내보내느니 실패한다.

    수집이 아니라 **표시** 시점에 난다 — 읽기 서비스가 `source` 문자열을
    표기로 바꿀 때다. 그래도 수집 계층 예외인 이유는, 출처 표기가 수집
    계약의 일부이기 때문이다 (ADR-013).
    """
