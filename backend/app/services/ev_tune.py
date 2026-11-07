"""노력치 미세조정 유스케이스.

**스키마 ↔ 도메인 변환만 한다.** 계산은 전부 `domain/ev_solver.py` 가 하고,
라우터는 이 함수 하나를 부른다 (CLAUDE.md 2장: 도메인 로직을 라우터에 두지 않는다).
"""

from __future__ import annotations

from typing import Final

from app.core.seed import item_catalog, nature_catalog, type_chart
from app.domain import ev_solver
from app.domain.damage import Weather
from app.domain.ev_solver import (
    AllocationPriority,
    BattleContext,
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
    BattleContextIn,
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

_WEATHER: Final[dict[str, Weather]] = {
    "clear": Weather.CLEAR,
    "sun": Weather.SUN,
    "rain": Weather.RAIN,
    "sand": Weather.SAND,
    "snow": Weather.SNOW,
}

# 어떤 요청에도 해당하는 전제. 숫자만 주고 전제를 숨기면 근거가 아니라 주장이 된다 (1-3).
_BASE_ASSUMPTIONS: Final[tuple[str, ...]] = (
    "데미지 보정 체인은 A안(ADR-003)을 따른다. 게임 내 실측으로 확정되지 않은 잠정 결정이다.",
    "상대 스펙은 요청에 주어진 보정 전 실수치에 랭크·도구를 적용한 값이다. "
    "메타 표본에서 추정하지 않았다.",
    "특성 보정은 적용하지 않았다. 특성 시드 데이터가 아직 없다.",
    "모래바람·설경은 데미지 배율이 아니라 스탯 단계 효과라 반영되지 않는다. "
    "챔피언스 수치가 확인되지 않았다.",
)

_ITEM_ASSUMPTION: Final[str] = (
    "도구 수치 대부분은 본가 기준 가정값이다(items.json 의 source: mainline-assumed). "
    "챔피언스에서 확인된 것은 생명의구슬 1.3 뿐이다."
)


def _assumptions_for(request: EvTuneRequest) -> list[str]:
    """이 요청에 실제로 해당하는 전제만 싣는다.

    해당 없는 문장을 늘 붙이면 사용자가 읽지 않게 된다.
    """
    notes = list(_BASE_ASSUMPTIONS)
    uses_item = request.item is not None or any(
        getattr(c, "opponent_item", None) is not None for c in request.constraints
    )
    if uses_item:
        notes.append(_ITEM_ASSUMPTION)
    return notes


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


def _to_context(data: BattleContextIn) -> BattleContext:
    return BattleContext(
        weather=_WEATHER[data.weather],
        is_critical=data.is_critical,
        screen=data.screen,
        field_multiplier=data.field_multiplier,
    )


def _to_constraint(data: ConstraintIn) -> Constraint:
    if isinstance(data, OutspeedIn):
        return OutspeedConstraint(
            label=data.label,
            target_speed=data.target_speed,
            opponent_item_id=data.opponent_item,
            opponent_rank_stage=data.opponent_rank_stage,
        )
    if isinstance(data, KnockoutIn):
        return KnockoutConstraint(
            label=data.label,
            opponent=_to_defender(data.opponent),
            move=_to_move(data.move),
            hits=data.hits,
            opponent_item_id=data.opponent_item,
            opponent_rank_stage=data.opponent_rank_stage,
        )
    if isinstance(data, SurviveIn):
        return SurviveConstraint(
            label=data.label,
            opponent=_to_attacker(data.opponent),
            move=_to_move(data.move),
            hits=data.hits,
            opponent_item_id=data.opponent_item,
            opponent_rank_stage=data.opponent_rank_stage,
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
        item_id=request.item,
        rank_stages={StatKey[name]: stage for name, stage in request.rank_stages.items()},
    )
    constraints = [_to_constraint(c) for c in request.constraints]
    priority = AllocationPriority[request.priority] if request.priority else None

    result = ev_solver.solve(
        type_chart(),
        item_catalog(),
        target,
        constraints,
        priority=priority,
        context=_to_context(request.context),
    )
    assumptions = _assumptions_for(request)

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
