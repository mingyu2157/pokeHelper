"""도메인 계산 상수.

챔피언스는 Lv.50 고정, 개체값(IV) 31 고정이다.
아래 오프셋에는 그 두 상수가 **이미 반영돼 있다** (CLAUDE.md 5-1의 축약 공식).
따라서 계산 함수는 레벨이나 개체값을 파라미터로 받지 않는다.
"""

from typing import Final

LEVEL: Final[int] = 50
IV: Final[int] = 31

# 축약 공식:
#   HP   = 종족값 + HP_OFFSET + effort_points          (성격 보정 없음)
#   기타 = floor((종족값 + STAT_OFFSET + effort_points) * 성격보정)
HP_OFFSET: Final[int] = 75
STAT_OFFSET: Final[int] = 20

# 성격 보정 1.1 / 0.9 는 이진 부동소수점으로 정확히 표현되지 않는다.
# 계산이 IEEE754 반올림에 의존하지 않도록 정수 연산 (n * 분자) // 분모 로 처리한다.
NATURE_UP_NUMERATOR: Final[int] = 11
NATURE_DOWN_NUMERATOR: Final[int] = 9
NATURE_DENOMINATOR: Final[int] = 10

# 노력치 — 챔피언스는 포인트 방식이다. 기존 시리즈의 노력치 개념은 여기에 존재하지 않는다.
MIN_POINTS_PER_STAT: Final[int] = 0
MAX_POINTS_PER_STAT: Final[int] = 32
MAX_TOTAL_POINTS: Final[int] = 66

# 종족값 하한. HP 종족값 1은 껍질몬(Shedinja)뿐이고 챔피언스에 존재하지 않으므로,
# 이 값이 들어오면 계산이 아니라 데이터 오류로 취급한다.
MIN_BASE_STAT: Final[int] = 1
SHEDINJA_BASE_HP: Final[int] = 1

# 랭크(능력 변화). 본가와 동일하다 (2026-11-10 확인).
#   능력치: +n -> (2+n)/2 배,  -n -> 2/(2+n) 배
#   명중·회피는 (3+n)/3 으로 표가 다르지만 데미지 계산에는 쓰지 않는다.
# 분수라서 정수 연산으로 정확히 계산할 수 있다.
MAX_RANK_STAGE: Final[int] = 6
MIN_RANK_STAGE: Final[int] = -6
RANK_BASE: Final[int] = 2
MIN_ACTUAL_STAT: Final[int] = 1
