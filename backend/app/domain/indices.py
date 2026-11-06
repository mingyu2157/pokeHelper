"""결정력 / 내구력 — 커뮤니티 관용 지표 (CLAUDE.md 5-3).

```
결정력(offense_index) ≈ 공격실수치 × 기술위력 × 자속보정 × 기타보정
내구력(bulk_index)    ≈ HP실수치 × 방어실수치 / K
평균 데미지 비율      ≈ (결정력 / 내구력) × 타입상성
```

**표시 전용이다. 확정 n타 판정에 쓰지 않는다.**
그건 `damage.py` 의 16 roll 이 한다. 이 지표는 "빠른 감 잡기"용이고,
UI 에 근사값임을 반드시 명시해야 한다.

왜 틀리는가: 실제 공식에는 `floor` 가 두 번 들어가고 `+2` 상수가 붙는다.
이 근사식에는 둘 다 없다. 데미지가 작을수록 `+2` 의 비중이 커져서 오차가 커진다.
"""

from __future__ import annotations

from typing import Final

# damage.py 와 대조해 얻은 값 (2026-11-07, 표본 2,923건).
# 커뮤니티 통용값은 0.411 이고 실측 최적은 0.4167 이었다.
# 평균 오차 1.45% / 중앙 1.08% / 최대 14.58%.
#
# K 는 상수이므로 **내구력 순위에는 영향을 주지 않는다.**
# 값이 의미를 갖는 건 평균 데미지 비율을 추정할 때뿐이다.
BULK_CONSTANT: Final[float] = 0.417

NEUTRAL: Final[float] = 1.0


def offense_index(
    *,
    attack_stat: int,
    power: int,
    stab: float = NEUTRAL,
    other: float = NEUTRAL,
) -> float:
    """결정력. 공격 쪽 화력을 한 숫자로 뭉갠 관용 지표."""
    if attack_stat <= 0:
        raise ValueError(f"공격 실수치는 1 이상이어야 한다: {attack_stat}")
    if power < 0:
        raise ValueError(f"기술 위력은 음수일 수 없다: {power}")
    return attack_stat * power * stab * other


def bulk_index(*, hp_stat: int, defense_stat: int, k: float = BULK_CONSTANT) -> float:
    """내구력. HP 와 방어를 곱해 한 숫자로 뭉갠 관용 지표."""
    if hp_stat <= 0:
        raise ValueError(f"HP 실수치는 1 이상이어야 한다: {hp_stat}")
    if defense_stat <= 0:
        raise ValueError(f"방어 실수치는 1 이상이어야 한다: {defense_stat}")
    if k <= 0:
        raise ValueError(f"내구력 상수는 0보다 커야 한다: {k}")
    return hp_stat * defense_stat / k


def estimated_damage_ratio(
    offense: float,
    bulk: float,
    *,
    type_effectiveness: float,
) -> float:
    """평균 데미지 비율(0~1) 추정. **확정타 판정에 쓰지 말 것.**"""
    if bulk <= 0:
        raise ValueError(f"내구력은 0보다 커야 한다: {bulk}")
    return offense / bulk * type_effectiveness
