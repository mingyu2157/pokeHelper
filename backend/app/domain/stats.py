"""실수치 계산 (Lv.50 / 개체값 31 고정).

CLAUDE.md 5-1의 축약 공식:
    HP   = 종족값 + 75 + effort_points                          (성격 보정 없음)
    기타 = floor((종족값 + 20 + effort_points) * nature_mod)    (곱은 맨 마지막)

성격 보정을 포인트보다 먼저 곱하면 값이 어긋난다. 순서를 바꾸지 말 것.
"""

from app.domain.constants import (
    HP_OFFSET,
    MIN_BASE_STAT,
    NATURE_DENOMINATOR,
    NATURE_DOWN_NUMERATOR,
    NATURE_UP_NUMERATOR,
    SHEDINJA_BASE_HP,
    STAT_OFFSET,
)
from app.domain.exceptions import InvalidBaseStat
from app.domain.nature import NatureModifier

_NUMERATORS: dict[NatureModifier, int] = {
    NatureModifier.UP: NATURE_UP_NUMERATOR,
    NatureModifier.NEUTRAL: NATURE_DENOMINATOR,
    NatureModifier.DOWN: NATURE_DOWN_NUMERATOR,
}


def _require_valid_base_stat(base_stat: int) -> None:
    if base_stat < MIN_BASE_STAT:
        raise InvalidBaseStat(f"종족값 {base_stat} 은 계산 가능한 범위를 벗어났다.")


def calc_hp(base_stat: int, effort_points: int) -> int:
    """HP 실수치. 성격 보정을 받지 않는다.

    HP 종족값 1은 껍질몬뿐이고 챔피언스에 존재하지 않는다. 들어오면 데이터 오류로 본다.
    축약 공식을 그대로 적용하면 1 + 75 = 76 이라는 틀린 값이 나가기 때문에 막는다.
    """
    _require_valid_base_stat(base_stat)
    if base_stat == SHEDINJA_BASE_HP:
        raise InvalidBaseStat(
            "HP 종족값 1은 껍질몬(Shedinja)뿐이며 챔피언스에 존재하지 않는다. "
            "시드 데이터를 확인할 것."
        )
    return base_stat + HP_OFFSET + effort_points


def calc_stat(base_stat: int, effort_points: int, modifier: NatureModifier) -> int:
    """HP 를 제외한 실수치.

    성격 보정은 포인트를 더한 뒤 맨 마지막에 곱하고 내림한다.
    float 곱셈 대신 정수 연산을 쓴다 — 1.1 과 0.9 는 이진 부동소수점으로
    정확히 표현되지 않아서, 계산 결과가 IEEE754 반올림에 의존하게 된다.
    """
    _require_valid_base_stat(base_stat)
    raw = base_stat + STAT_OFFSET + effort_points
    return raw * _NUMERATORS[modifier] // NATURE_DENOMINATOR
