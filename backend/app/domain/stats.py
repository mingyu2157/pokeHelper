"""실수치 계산 (Lv.50 / 개체값 31 고정).

CLAUDE.md 5-1의 축약 공식:
    HP   = 종족값 + 75 + effort_points                          (성격 보정 없음)
    기타 = floor((종족값 + 20 + effort_points) * nature_mod)    (곱은 맨 마지막)

성격 보정을 포인트보다 먼저 곱하면 값이 어긋난다. 순서를 바꾸지 말 것.
"""

from app.domain.constants import (
    HP_OFFSET,
    MAX_POINTS_PER_STAT,
    MAX_RANK_STAGE,
    MIN_ACTUAL_STAT,
    MIN_BASE_STAT,
    MIN_RANK_STAGE,
    NATURE_DENOMINATOR,
    NATURE_DOWN_NUMERATOR,
    NATURE_UP_NUMERATOR,
    RANK_BASE,
    SHEDINJA_BASE_HP,
    STAT_OFFSET,
)
from app.domain.exceptions import InvalidBaseStat, InvalidRankStage
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


def is_magic_number(base_stat: int, effort_points: int, modifier: NatureModifier) -> bool:
    """상승보정에서 버림 손실이 0인 지점인가.

    `종족값 + 20 + 포인트` 가 10의 배수면 `× 11/10` 이 정확히 떨어져
    최종 실수치가 11의 배수가 된다. 커뮤니티에서 매직넘버라고 부른다.

    상승보정에만 의미가 있다. 무보정은 애초에 버림이 없고,
    하락보정은 반대로 정체 구간이 생긴다.
    """
    if modifier is not NatureModifier.UP:
        return False
    raw = base_stat + STAT_OFFSET + effort_points
    return raw % NATURE_DENOMINATOR == 0


def points_to_next_magic_number(
    base_stat: int, effort_points: int, modifier: NatureModifier
) -> int | None:
    """다음 매직넘버까지 필요한 추가 포인트. 상승보정이 아니거나 범위를 벗어나면 None.

    바로 직전 지점(`raw % 10 == 9`)에서 멈추면 1포인트로 실수치 2를 얻을 기회를 버린다.
    """
    if modifier is not NatureModifier.UP:
        return None
    raw = base_stat + STAT_OFFSET + effort_points
    needed = (NATURE_DENOMINATOR - raw % NATURE_DENOMINATOR) % NATURE_DENOMINATOR
    if needed == 0:
        return 0
    if effort_points + needed > MAX_POINTS_PER_STAT:
        return None
    return needed


def apply_rank_stage(actual_stat: int, stage: int) -> int:
    """랭크 단계를 실수치에 적용한다.

    상승은 `(2+n)/2` 배, 하락은 `2/(2+n)` 배다 (본가와 동일, 2026-11-10 확인).
    분수를 그대로 정수 연산으로 쓴다 — float 를 거치면 경계에서 어긋난다.

    **이것은 스탯 단계 보정이다** (ADR-007). 데미지에 곱하는 배율이 아니라
    기초 계산에 들어가는 실수치 자체를 바꾼다.
    """
    if actual_stat < MIN_ACTUAL_STAT:
        raise InvalidBaseStat(f"실수치는 {MIN_ACTUAL_STAT} 이상이어야 한다: {actual_stat}")
    if not MIN_RANK_STAGE <= stage <= MAX_RANK_STAGE:
        raise InvalidRankStage(f"랭크 단계는 {MIN_RANK_STAGE} ~ {MAX_RANK_STAGE} 범위다: {stage}")

    if stage >= 0:
        numerator, denominator = RANK_BASE + stage, RANK_BASE
    else:
        numerator, denominator = RANK_BASE, RANK_BASE - stage

    return max(actual_stat * numerator // denominator, MIN_ACTUAL_STAT)
