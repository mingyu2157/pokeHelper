"""`/calc/ev-tune` 요청·응답 스키마.

**상대 스펙을 실수치로 직접 받는다.** 포켓몬·기술 이름으로 받으려면
종족값·기술 수치 시드 데이터가 필요한데 아직 없다 (Phase 2).
시드가 생기면 이름 → 실수치 변환 계층만 앞에 얹으면 되도록, 이 스키마는
그대로 두고 별도 엔드포인트나 선택 필드로 확장한다.

식별자는 영문이다 (CLAUDE.md 4장). 한글 표시는 프론트 몫이다.
"""

from __future__ import annotations

from typing import Annotated, Final, Literal

from pydantic import BaseModel, Field

# ── 입력 상한 ────────────────────────────────────────────────────────────
# 근거: championsbattledata 폼 447개 전수 조사 (2026-11-10).
#   종족값 최대 250 (Mega Aggron 방어), HP 최대 235 (Snorlax)
#   HP 실수치 상한   = 235 + 75 + 32 = 342
#   기타 실수치 상한 = floor((250 + 20 + 32) * 1.1) = 332
#
# **실수치는 보정 전 순수 값이다.** 랭크·도구는 별도 필드로 받고 엔진이 적용한다.
# 보정된 값을 실수치에 직접 넣으면 랭크가 두 번 걸린다.
#
# 종족값 상한만 관측값 250 이 아니라 255 로 둔다 — 챔피언스에 포켓몬이 계속
# 추가되므로 시리즈 이론 상한까지 열어둔다.
MAX_BASE_STAT: Final[int] = 255
MAX_ACTUAL_HP: Final[int] = 342
MAX_ACTUAL_STAT: Final[int] = 332
MAX_MOVE_POWER: Final[int] = 300
# 최대 만족 부분집합 탐색이 2^n 이라 제약 수에 상한이 필요하다.
MAX_CONSTRAINTS: Final[int] = 8
# 확정 n타 — 실전에서 n 이 커질 일이 없다.
MAX_HITS: Final[int] = 8
# 랭크(능력 변화)는 -6 ~ +6 이다.
MAX_RANK_STAGE: Final[int] = 6
# 벽·필드 배율. 0 은 무효를 뜻하므로 받지 않는다 — 무효는 도구·타입으로 표현한다.
MIN_MULTIPLIER: Final[float] = 0.1
MAX_MULTIPLIER: Final[float] = 4.0

StatName = Literal["HP", "ATTACK", "DEFENSE", "SP_ATTACK", "SP_DEFENSE", "SPEED"]
PriorityName = Literal[
    "PHYSICAL_OFFENSE", "SPECIAL_OFFENSE", "PHYSICAL_BULK", "SPECIAL_BULK", "SPEED"
]
MoveCategoryName = Literal["physical", "special", "status"]
WeatherName = Literal["clear", "sun", "rain", "sand", "snow"]

RankStage = Annotated[int, Field(ge=-MAX_RANK_STAGE, le=MAX_RANK_STAGE)]
Multiplier = Annotated[float, Field(ge=MIN_MULTIPLIER, le=MAX_MULTIPLIER)]

TypeList = Annotated[list[str], Field(min_length=1, max_length=2)]


BaseStatValue = Annotated[int, Field(gt=0, le=MAX_BASE_STAT)]
ActualStatValue = Annotated[int, Field(gt=0, le=MAX_ACTUAL_STAT)]
ActualHpValue = Annotated[int, Field(gt=0, le=MAX_ACTUAL_HP)]


class BaseStatsIn(BaseModel):
    hp: BaseStatValue
    attack: BaseStatValue
    defense: BaseStatValue
    sp_attack: BaseStatValue
    sp_defense: BaseStatValue
    speed: BaseStatValue


class MoveIn(BaseModel):
    power: int = Field(ge=0, le=MAX_MOVE_POWER)
    type: str
    category: MoveCategoryName


class AttackerIn(BaseModel):
    """상대 공격 측. **보정 전 순수 실수치**다 — 랭크·도구는 제약의 별도 필드로 받는다."""

    attack_stat: ActualStatValue
    special_attack_stat: ActualStatValue
    types: TypeList
    is_burned: bool = False


class DefenderIn(BaseModel):
    """상대 방어 측. **보정 전 순수 실수치**다 — 랭크·도구는 제약의 별도 필드로 받는다."""

    defense_stat: ActualStatValue
    special_defense_stat: ActualStatValue
    hp_stat: ActualHpValue
    types: TypeList


class BattleContextIn(BaseModel):
    """대면 상황. 제약 전체에 공통으로 걸린다."""

    weather: WeatherName = "clear"
    is_critical: bool = False
    screen: Multiplier = Field(
        default=1.0,
        description="벽 배율. 리플렉터·빛의장막·오로라베일은 모두 0.5 다.",
    )
    field_multiplier: Multiplier = Field(
        default=1.0, description="필드 배율. 챔피언스 수치가 확인되지 않아 호출자가 정한다."
    )


class OutspeedIn(BaseModel):
    kind: Literal["outspeed"]
    label: str
    target_speed: ActualStatValue = Field(
        description="상대의 보정 전 스피드 실수치. 도구·랭크는 아래 필드로 받는다."
    )
    opponent_item: str | None = None
    opponent_rank_stage: RankStage = 0


class KnockoutIn(BaseModel):
    kind: Literal["knockout"]
    label: str
    opponent: DefenderIn
    move: MoveIn
    hits: int = Field(default=1, ge=1, le=MAX_HITS)
    opponent_item: str | None = None
    opponent_rank_stage: RankStage = 0


class SurviveIn(BaseModel):
    kind: Literal["survive"]
    label: str
    opponent: AttackerIn
    move: MoveIn
    hits: int = Field(default=1, ge=1, le=MAX_HITS)
    opponent_item: str | None = None
    opponent_rank_stage: RankStage = 0


ConstraintIn = Annotated[OutspeedIn | KnockoutIn | SurviveIn, Field(discriminator="kind")]


class EvTuneRequest(BaseModel):
    base_stats: BaseStatsIn
    types: TypeList
    nature: str | None = Field(
        default=None,
        description="성격 영문 식별자 (예: jolly). 생략하면 무보정으로 본다.",
    )
    priority: PriorityName | None = Field(
        default=None,
        description="남은 포인트를 부을 곳. 생략하면 제약 만족에 필요한 만큼만 쓴다.",
    )
    item: str | None = Field(default=None, description="우리 도구 영문 식별자 (예: lifeorb).")
    rank_stages: dict[StatName, RankStage] = Field(
        default_factory=dict, description="랭크(능력 변화). HP 는 받지 않는다."
    )
    context: BattleContextIn = Field(default_factory=BattleContextIn)
    constraints: list[ConstraintIn] = Field(default_factory=list, max_length=MAX_CONSTRAINTS)


class SpreadOut(BaseModel):
    hp: int
    attack: int
    defense: int
    sp_attack: int
    sp_defense: int
    speed: int
    total: int
    notation: str = Field(description="UI 표준 HABCDS 표기 (예: A10C24S32)")


class JustificationOut(BaseModel):
    stat: StatName
    points: int
    constraint: str
    detail: str


class OptionOut(BaseModel):
    satisfied: list[str]
    dropped: list[str]
    spread: SpreadOut
    justifications: list[JustificationOut]


class EvTuneSolution(BaseModel):
    feasible: Literal[True] = True
    nature: str | None
    spread: SpreadOut
    justifications: list[JustificationOut]
    alternatives: list[str]
    assumptions: list[str]


class EvTuneInfeasible(BaseModel):
    feasible: Literal[False] = False
    nature: str | None
    budget: int
    total_required: int
    per_constraint_points: dict[str, int]
    impossible: list[str]
    options: list[OptionOut]
    assumptions: list[str]


EvTuneResponse = Annotated[EvTuneSolution | EvTuneInfeasible, Field(discriminator="feasible")]
