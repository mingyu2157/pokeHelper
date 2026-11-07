"""`/calc/ev-tune` 요청·응답 스키마.

**상대 스펙을 실수치로 직접 받는다.** 포켓몬·기술 이름으로 받으려면
종족값·기술 수치 시드 데이터가 필요한데 아직 없다 (Phase 2).
시드가 생기면 이름 → 실수치 변환 계층만 앞에 얹으면 되도록, 이 스키마는
그대로 두고 별도 엔드포인트나 선택 필드로 확장한다.

식별자는 영문이다 (CLAUDE.md 4장). 한글 표시는 프론트 몫이다.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, Field

StatName = Literal["HP", "ATTACK", "DEFENSE", "SP_ATTACK", "SP_DEFENSE", "SPEED"]
PriorityName = Literal[
    "PHYSICAL_OFFENSE", "SPECIAL_OFFENSE", "PHYSICAL_BULK", "SPECIAL_BULK", "SPEED"
]
MoveCategoryName = Literal["physical", "special", "status"]

TypeList = Annotated[list[str], Field(min_length=1, max_length=2)]


class BaseStatsIn(BaseModel):
    hp: int = Field(gt=0)
    attack: int = Field(gt=0)
    defense: int = Field(gt=0)
    sp_attack: int = Field(gt=0)
    sp_defense: int = Field(gt=0)
    speed: int = Field(gt=0)


class MoveIn(BaseModel):
    power: int = Field(ge=0)
    type: str
    category: MoveCategoryName


class AttackerIn(BaseModel):
    """상대 공격 측. 도구·랭크가 이미 반영된 실수치다 (ADR-007)."""

    attack_stat: int = Field(gt=0)
    special_attack_stat: int = Field(gt=0)
    types: TypeList
    is_burned: bool = False


class DefenderIn(BaseModel):
    """상대 방어 측. 도구·랭크가 이미 반영된 실수치다 (ADR-007)."""

    defense_stat: int = Field(gt=0)
    special_defense_stat: int = Field(gt=0)
    hp_stat: int = Field(gt=0)
    types: TypeList


class OutspeedIn(BaseModel):
    kind: Literal["outspeed"]
    label: str
    target_speed: int = Field(gt=0)


class KnockoutIn(BaseModel):
    kind: Literal["knockout"]
    label: str
    opponent: DefenderIn
    move: MoveIn
    hits: int = Field(default=1, ge=1)


class SurviveIn(BaseModel):
    kind: Literal["survive"]
    label: str
    opponent: AttackerIn
    move: MoveIn
    hits: int = Field(default=1, ge=1)


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
    constraints: list[ConstraintIn] = Field(default_factory=list)


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
