"""노력치 미세조정 유스케이스.

**스키마 ↔ 도메인 변환만 한다.** 계산은 전부 `domain/ev_solver.py` 가 하고,
라우터는 이 함수 하나를 부른다 (CLAUDE.md 2장: 도메인 로직을 라우터에 두지 않는다).
"""

from __future__ import annotations

from typing import Final

from app.core.seed import nature_catalog, type_chart
from app.domain import ev_solver
from app.domain.ev_solver import (
    AllocationPriority,
    BuildTarget,
    Constraint,
    Infeasible,
    KnockoutConstraint,
    OutspeedConstraint,
    Solution,
    SurviveConstraint,
)
from app.domain.exceptions import UnknownPokemonType
from app.domain.models import Attacker, BaseStats, Defender, MoveCategory, MoveSpec
from app.domain.nature import StatKey
from app.domain.typechart import PokemonType
from app.schemas.ev_tune import (
    AttackerIn,
    ConstraintIn,
    DefenderIn,
    EvTuneInfeasible,
    EvTuneRequest,
    EvTuneSolution,
    JustificationOut,
    KnockoutIn,
    MoveIn,
    OptionOut,
    OutspeedIn,
    SpreadOut,
    SurviveIn,
)

# UI 표준 HABCDS 표기 (CLAUDE.md 4장). 표기 순서가 곧 H-A-B-C-D-S 다.
_NOTATION_ORDER: Final[tuple[tuple[str, str], ...]] = (
    ("H", "hp"),
    ("A", "attack"),
    ("B", "defense"),
    ("C", "sp_attack"),
    ("D", "sp_defense"),
    ("S", "speed"),
)
_EMPTY_NOTATION: Final[str] = "-"

_CATEGORIES: Final[dict[str, MoveCategory]] = {
    "physical": MoveCategory.PHYSICAL,
    "special": MoveCategory.SPECIAL,
    "status": MoveCategory.STATUS,
}

# 어떤 요청에도 해당하는 전제. 숫자만 주고 전제를 숨기면 근거가 아니라 주장이 된다 (1-3).
_BASE_ASSUMPTIONS: Final[tuple[str, ...]] = (
    "데미지 보정 체인은 A안(ADR-003)을 따른다. 게임 내 실측으로 확정되지 않은 잠정 결정이다.",
    "도구·특성·날씨·벽·필드 보정은 적용하지 않았다. 해석 계층이 아직 없다.",
    "상대 스펙은 요청에 주어진 실수치를 그대로 사용했다. 메타 표본에서 추정하지 않았다.",
)


def _parse_type(raw: str) -> PokemonType:
    try:
        return PokemonType(raw)
    except ValueError as error:
        raise UnknownPokemonType(f"알 수 없는 타입: {raw!r}") from error


def _parse_types(raw: list[str]) -> tuple[PokemonType, ...]:
    return tuple(_parse_type(value) for value in raw)


def _to_move(move: MoveIn) -> MoveSpec:
    return MoveSpec(
        power=move.power,
        move_type=_parse_type(move.type),
        category=_CATEGORIES[move.category],
    )


def _to_attacker(data: AttackerIn) -> Attacker:
    return Attacker(
        attack_stat=data.attack_stat,
        special_attack_stat=data.special_attack_stat,
        types=_parse_types(data.types),
        is_burned=data.is_burned,
    )


def _to_defender(data: DefenderIn) -> Defender:
    return Defender(
        defense_stat=data.defense_stat,
        special_defense_stat=data.special_defense_stat,
        hp_stat=data.hp_stat,
        types=_parse_types(data.types),
    )


def _to_constraint(data: ConstraintIn) -> Constraint:
    if isinstance(data, OutspeedIn):
        return OutspeedConstraint(label=data.label, target_speed=data.target_speed)
    if isinstance(data, KnockoutIn):
        return KnockoutConstraint(
            label=data.label,
            opponent=_to_defender(data.opponent),
            move=_to_move(data.move),
            hits=data.hits,
        )
    if isinstance(data, SurviveIn):
        return SurviveConstraint(
            label=data.label,
            opponent=_to_attacker(data.opponent),
            move=_to_move(data.move),
            hits=data.hits,
        )
    raise AssertionError(f"처리되지 않은 제약 종류: {type(data).__name__}")


def _to_spread_out(spread: object) -> SpreadOut:
    values = {field: getattr(spread, field) for _, field in _NOTATION_ORDER}
    notation = "".join(
        f"{letter}{values[field]}" for letter, field in _NOTATION_ORDER if values[field]
    )
    return SpreadOut(
        **values,
        total=sum(values.values()),
        notation=notation or _EMPTY_NOTATION,
    )


def _to_justifications(items: tuple[ev_solver.Justification, ...]) -> list[JustificationOut]:
    return [
        JustificationOut(
            stat=j.stat.name,  # type: ignore[arg-type]
            points=j.points,
            constraint=j.constraint_label,
            detail=j.detail,
        )
        for j in items
    ]


def tune(request: EvTuneRequest) -> EvTuneSolution | EvTuneInfeasible:
    """요청을 도메인으로 옮기고, 결과를 응답 스키마로 되돌린다."""
    natures: dict[StatKey, object] = {}
    if request.nature is not None:
        natures = nature_catalog().get(request.nature).modifiers()  # type: ignore[assignment]

    target = BuildTarget(
        base_stats=BaseStats(**request.base_stats.model_dump()),
        natures=natures,  # type: ignore[arg-type]
        types=_parse_types(request.types),
    )
    constraints = [_to_constraint(c) for c in request.constraints]
    priority = AllocationPriority[request.priority] if request.priority else None

    result = ev_solver.solve(type_chart(), target, constraints, priority=priority)
    assumptions = list(_BASE_ASSUMPTIONS)

    if isinstance(result, Solution):
        return EvTuneSolution(
            nature=request.nature,
            spread=_to_spread_out(result.spread),
            justifications=_to_justifications(result.justifications),
            alternatives=list(result.alternatives),
            assumptions=assumptions,
        )

    infeasible: Infeasible = result
    return EvTuneInfeasible(
        nature=request.nature,
        budget=infeasible.budget,
        total_required=infeasible.total_required,
        per_constraint_points=dict(infeasible.per_constraint_points),
        impossible=list(infeasible.impossible),
        options=[
            OptionOut(
                satisfied=list(option.satisfied),
                dropped=list(option.dropped),
                spread=_to_spread_out(option.spread),
                justifications=_to_justifications(option.justifications),
            )
            for option in infeasible.options
        ],
        assumptions=assumptions,
    )
