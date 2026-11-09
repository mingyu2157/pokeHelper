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


class InvalidRankStage(DomainError):
    """랭크 단계가 -6 ~ +6 범위를 벗어났다."""


class UnknownPokemon(DomainError):
    """마스터 시드에 없는 포켓몬이다."""


class UnknownMove(DomainError):
    """마스터 시드에 없는 기술이다."""


class UnknownMoveCategory(DomainError):
    """기술 데이터에 알 수 없는 분류가 들어 있다."""


class MoveHasNoFixedPower(DomainError):
    """위력이 상황에 따라 결정되는 기술을 고정 위력처럼 쓰려 했다.

    자이로볼·지구던지기·카운터처럼 위력이 스피드·체중·받은 데미지로 정해지는 기술이다.
    호출자가 위력을 계산해 넘겨야 한다. **0 으로 때우면 안 된다** — 데미지가 0 이
    아니라 '아직 모른다'이고, 그 둘을 섞으면 "이 기술로는 안 쓰러진다"는 틀린 답이 나온다.
    """


class MoveIsNotDamaging(DomainError):
    """변화 기술로 데미지를 계산하려 했다."""


class NameNotMapped(DomainError):
    """한영 명칭 매핑이 없다.

    영문명을 그대로 통과시키거나 비슷한 이름을 찾아주지 않는다 (ADR-005 fail-closed).
    매핑이 틀리면 화면에는 멀쩡한 한글이 뜨고 뒤에서 엉뚱한 포켓몬으로 계산이 돌아간다.
    """


class DuplicateNameMapping(DomainError):
    """한 한글명이 두 개 이상의 id 에 걸려 있다. 양방향 유일성 위반이다."""
