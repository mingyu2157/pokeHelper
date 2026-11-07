"""도메인 계층 예외.

이 계층은 DB/HTTP/LLM을 모르므로 예외도 여기서 자급한다.
HTTP 상태코드 매핑은 api 계층이 이 예외들을 import 해서 처리한다 (방향: api -> domain).
"""


class DomainError(Exception):
    """도메인 규칙 위반의 최상위 예외."""


class InvalidEffortSpread(DomainError):
    """노력치 배분이 규칙(스탯당 0~32, 총합 66 이하)을 위반했다."""


class InvalidBaseStat(DomainError):
    """종족값이 계산 가능한 범위를 벗어났거나 지원하지 않는 특수 케이스다."""


class UnknownPokemonType(DomainError):
    """타입 상성 데이터에 알 수 없는 타입 이름이 들어 있다."""


class UnknownItemEffect(DomainError):
    """도구 데이터에 알 수 없는 효과 종류가 들어 있다."""


class InvalidNatureSpread(DomainError):
    """성격 보정이 불가능한 조합이다.

    상승·하락 보정은 각각 한 스탯에만 걸리고, HP 는 보정을 받지 않는다.
    """


class UnknownNature(DomainError):
    """성격 데이터에 알 수 없는 이름이나 스탯이 들어 있다."""
