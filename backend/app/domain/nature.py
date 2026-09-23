"""스탯 키와 성격 보정.

성격 이름(Adamant 등) -> 상승/하락 스탯 매핑은 아직 두지 않는다.
실제로 사용하는 시점에 출처와 교차검증해서 추가한다.
여기에는 계산에 필요한 보정 단계만 있다.
"""

from enum import Enum, auto


class StatKey(Enum):
    """스탯 식별자. UI 표기 HABCDS 와 1:1 대응한다."""

    HP = auto()
    ATTACK = auto()
    DEFENSE = auto()
    SP_ATTACK = auto()
    SP_DEFENSE = auto()
    SPEED = auto()


class NatureModifier(Enum):
    """성격 보정 단계.

    float 을 직접 받지 않는 이유는 1.1/1.0/0.9 외의 값이 계산에 들어오는 걸 막기 위해서다.
    """

    UP = auto()
    NEUTRAL = auto()
    DOWN = auto()
