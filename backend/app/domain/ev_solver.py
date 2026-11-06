"""노력치 미세조정 솔버 (CLAUDE.md 5-4).

사용자가 "이 상대를 추월하고 싶다 / 이 기술을 버티고 싶다"를 제약으로 주면
그걸 만족하는 최소 배분을 찾는다.

**이 서비스의 차별화 지점이다.** 경쟁 제품은 정형 배분(AS풀보정 등)을 제시하는 데서
끝나지만, 여기서는 목표를 역산하고 **왜 그 포인트인지**를 함께 돌려준다.
사용자가 배워야 하는 건 숫자가 아니라 근거다 (규칙 6).

판정은 포인트가 아니라 **최종 실수치**로 한다 — 포인트와 실수치는 선형이 아니다
(CLAUDE.md 5-1 귀결 2).

해가 없으면 근사치를 정답인 척 내놓지 않는다. 무엇이 충돌하는지 알리고,
포기할 수 있는 조합을 근거와 함께 제시한다 (규칙 5).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from itertools import combinations, product
from typing import Final

from app.domain.constants import (
    MAX_POINTS_PER_STAT,
    MAX_TOTAL_POINTS,
    MIN_POINTS_PER_STAT,
)
from app.domain.damage import calculate
from app.domain.effort import EffortSpread
from app.domain.models import Attacker, BaseStats, Defender, MoveSpec
from app.domain.nature import NatureModifier, StatKey
from app.domain.stats import calc_hp, calc_stat
from app.domain.typechart import PokemonType, TypeChart

_POINT_RANGE: Final[range] = range(MIN_POINTS_PER_STAT, MAX_POINTS_PER_STAT + 1)

_STAT_FIELDS: Final[Mapping[StatKey, str]] = {
    StatKey.HP: "hp",
    StatKey.ATTACK: "attack",
    StatKey.DEFENSE: "defense",
    StatKey.SP_ATTACK: "sp_attack",
    StatKey.SP_DEFENSE: "sp_defense",
    StatKey.SPEED: "speed",
}


# ---------------------------------------------------------------- 입력


@dataclass(frozen=True)
class BuildTarget:
    """조정할 우리 포켓몬. 성격은 스탯별 보정으로 받는다."""

    base_stats: BaseStats
    natures: Mapping[StatKey, NatureModifier] = field(default_factory=dict)
    types: tuple[PokemonType, ...] = ()

    def modifier(self, stat: StatKey) -> NatureModifier:
        return self.natures.get(stat, NatureModifier.NEUTRAL)

    def actual(self, stat: StatKey, points: int) -> int:
        base = self.base_stats.of(stat)
        if stat is StatKey.HP:
            return calc_hp(base, points)
        return calc_stat(base, points, self.modifier(stat))


@dataclass(frozen=True)
class OutspeedConstraint:
    """상대 스피드 실수치를 넘는다. 동속은 추월이 아니다."""

    label: str
    target_speed: int


@dataclass(frozen=True)
class KnockoutConstraint:
    """상대를 확정 n타로 잡는다. 최소 roll 로도 쓰러져야 한다."""

    label: str
    opponent: Defender
    move: MoveSpec
    hits: int = 1


@dataclass(frozen=True)
class SurviveConstraint:
    """상대 기술을 n회 맞아도 쓰러지지 않는다. 최대 roll 로도 버텨야 한다."""

    label: str
    opponent: Attacker
    move: MoveSpec
    hits: int = 1


type Constraint = OutspeedConstraint | KnockoutConstraint | SurviveConstraint


# ---------------------------------------------------------------- 출력


@dataclass(frozen=True)
class Justification:
    """이 포인트가 무엇을 위한 것인가 (규칙 6)."""

    stat: StatKey
    points: int
    constraint_label: str
    detail: str


@dataclass(frozen=True)
class Solution:
    spread: EffortSpread
    justifications: tuple[Justification, ...]
    alternatives: tuple[str, ...] = ()


@dataclass(frozen=True)
class Option:
    """충돌 시 제시하는 선택지 — 무엇을 얻고 무엇을 포기하는가."""

    satisfied: tuple[str, ...]
    dropped: tuple[str, ...]
    spread: EffortSpread
    justifications: tuple[Justification, ...]


@dataclass(frozen=True)
class Infeasible:
    """해가 없다. 왜 없는지를 두 가지로 나눠 알린다.

    - `impossible`: 포인트를 아무리 써도 만족할 수 없는 제약 (상한을 넘는 목표)
    - 예산 초과: 각각은 가능하지만 합이 66을 넘는 경우

    둘을 같은 숫자로 뭉개면 "0포인트면 되는데 왜 안 되지?"가 된다.
    """

    budget: int
    per_constraint_points: dict[str, int]
    impossible: tuple[str, ...] = ()
    options: tuple[Option, ...] = ()

    @property
    def total_required(self) -> int:
        """만족 가능한 제약들의 최소 비용 합. 불가능한 제약은 세지 않는다."""
        return sum(self.per_constraint_points.values())


type SolverResult = Solution | Infeasible


# ---------------------------------------------------------------- 내부


@dataclass(frozen=True)
class _Candidate:
    """한 제약을 만족시키는 하나의 배분안과 그 근거."""

    points: Mapping[StatKey, int]
    justifications: tuple[Justification, ...]

    @property
    def total(self) -> int:
        return sum(self.points.values())


def _spread_from(points: Mapping[StatKey, int]) -> EffortSpread:
    return EffortSpread(**{_STAT_FIELDS[stat]: value for stat, value in points.items()})


def _outspeed_candidates(
    target: BuildTarget, constraint: OutspeedConstraint
) -> tuple[_Candidate, ...]:
    for points in _POINT_RANGE:
        actual = target.actual(StatKey.SPEED, points)
        if actual > constraint.target_speed:
            detail = f"{constraint.label}({constraint.target_speed}) 추월 — 실수치 {actual}"
            return (
                _Candidate(
                    points={StatKey.SPEED: points},
                    justifications=(
                        Justification(StatKey.SPEED, points, constraint.label, detail),
                    ),
                ),
            )
    return ()


def _knockout_candidates(
    chart: TypeChart, target: BuildTarget, constraint: KnockoutConstraint
) -> tuple[_Candidate, ...]:
    stat = target.base_stats.offensive_key(constraint.move.category)
    for points in _POINT_RANGE:
        attack = target.actual(stat, points)
        attacker = Attacker(attack_stat=attack, special_attack_stat=attack, types=target.types)
        result = calculate(chart, attacker, constraint.opponent, constraint.move)
        if result.rolls[0] * constraint.hits >= constraint.opponent.hp_stat:
            detail = (
                f"{constraint.label} 확정 {constraint.hits}타 — "
                f"최소 roll {result.rolls[0]} × {constraint.hits} "
                f"≥ HP {constraint.opponent.hp_stat}"
            )
            return (
                _Candidate(
                    points={stat: points},
                    justifications=(Justification(stat, points, constraint.label, detail),),
                ),
            )
    return ()


def _survive_candidates(
    chart: TypeChart, target: BuildTarget, constraint: SurviveConstraint
) -> tuple[_Candidate, ...]:
    """HP 와 방어 두 축이라 파레토 전선을 만든다.

    "HP 32 / 방어 0" 과 "HP 16 / 방어 16" 이 같은 제약을 만족할 수 있다.
    총 투자가 더 적거나 같으면서 다른 축도 더 적은 안만 남긴다.
    """
    defense_stat = target.base_stats.defensive_key(constraint.move.category)
    frontier: list[_Candidate] = []
    best_defense_points = MAX_POINTS_PER_STAT + 1

    for hp_points in _POINT_RANGE:
        hp = target.actual(StatKey.HP, hp_points)
        for defense_points in range(MIN_POINTS_PER_STAT, best_defense_points):
            defense = target.actual(defense_stat, defense_points)
            defender = Defender(
                defense_stat=defense,
                special_defense_stat=defense,
                hp_stat=hp,
                types=target.types,
            )
            result = calculate(chart, constraint.opponent, defender, constraint.move)
            if result.rolls[-1] * constraint.hits < hp:
                detail = (
                    f"{constraint.label} {constraint.hits}회 버팀 — "
                    f"최대 roll {result.rolls[-1]} × {constraint.hits} < HP {hp}"
                )
                points: dict[StatKey, int] = {}
                justifications: list[Justification] = []
                if hp_points:
                    points[StatKey.HP] = hp_points
                    justifications.append(
                        Justification(StatKey.HP, hp_points, constraint.label, detail)
                    )
                if defense_points:
                    points[defense_stat] = defense_points
                    justifications.append(
                        Justification(defense_stat, defense_points, constraint.label, detail)
                    )
                if not justifications:
                    justifications.append(Justification(StatKey.HP, 0, constraint.label, detail))
                frontier.append(_Candidate(points=points, justifications=tuple(justifications)))
                best_defense_points = defense_points
                break
        if best_defense_points == MIN_POINTS_PER_STAT:
            break
    return tuple(frontier)


def _candidates_for(
    chart: TypeChart, target: BuildTarget, constraint: Constraint
) -> tuple[_Candidate, ...]:
    if isinstance(constraint, OutspeedConstraint):
        return _outspeed_candidates(target, constraint)
    if isinstance(constraint, KnockoutConstraint):
        return _knockout_candidates(chart, target, constraint)
    return _survive_candidates(chart, target, constraint)


def _combine(choices: Sequence[_Candidate]) -> tuple[dict[StatKey, int], int]:
    """제약마다 고른 안을 합친다. 같은 스탯은 더 큰 요구를 따른다."""
    merged: dict[StatKey, int] = {}
    for candidate in choices:
        for stat, value in candidate.points.items():
            merged[stat] = max(merged.get(stat, 0), value)
    return merged, sum(merged.values())


def _best_combination(
    candidate_sets: Sequence[tuple[_Candidate, ...]],
) -> tuple[dict[StatKey, int], tuple[Justification, ...], int] | None:
    """총 투자가 가장 적은 조합. 예산을 넘으면 None."""
    best: tuple[dict[StatKey, int], tuple[Justification, ...], int] | None = None
    for choices in product(*candidate_sets):
        merged, total = _combine(choices)
        if total > MAX_TOTAL_POINTS:
            continue
        if best is None or total < best[2] or (total == best[2] and _prefers_hp(merged, best[0])):
            justifications = tuple(j for c in choices for j in c.justifications)
            best = (merged, justifications, total)
    return best


def _prefers_hp(candidate: Mapping[StatKey, int], current: Mapping[StatKey, int]) -> bool:
    """총합이 같으면 HP 쪽에 실린 안을 고른다 (②번 결정 A안).

    HP 는 물리·특수 양쪽 내구에 작용하므로 같은 비용이면 더 넓게 쓰인다.
    """
    return candidate.get(StatKey.HP, 0) > current.get(StatKey.HP, 0)


def _minimum_total(candidates: tuple[_Candidate, ...]) -> int:
    return min((c.total for c in candidates), default=0)


def _alternatives_for(
    constraints: Iterable[Constraint], points: Mapping[StatKey, int]
) -> tuple[str, ...]:
    """솔버가 축을 정했지만 사용자가 직접 고를 수도 있음을 알린다 (②번)."""
    notes: list[str] = []
    for constraint in constraints:
        if isinstance(constraint, SurviveConstraint):
            notes.append(
                f"'{constraint.label}' 은 HP 와 방어 어느 쪽에 실어도 만족할 수 있다. "
                f"현재는 총 투자가 가장 적은 배분(HP {points.get(StatKey.HP, 0)})을 골랐다. "
                "방어 쪽으로 몰아주는 조정도 가능하다."
            )
    return tuple(notes)


# ---------------------------------------------------------------- 공개 API


def solve(
    chart: TypeChart,
    target: BuildTarget,
    constraints: Sequence[Constraint],
) -> SolverResult:
    """제약을 만족하는 최소 배분을 찾는다. 없으면 무엇이 충돌하는지 알린다."""
    if not constraints:
        return Solution(spread=EffortSpread(), justifications=())

    candidate_sets = [_candidates_for(chart, target, c) for c in constraints]
    pairs = list(zip(constraints, candidate_sets, strict=True))

    impossible = tuple(c.label for c, candidates in pairs if not candidates)
    per_constraint = {c.label: _minimum_total(candidates) for c, candidates in pairs if candidates}

    if not impossible:
        best = _best_combination(candidate_sets)
        if best is not None:
            merged, justifications, _ = best
            return Solution(
                spread=_spread_from(merged),
                justifications=justifications,
                alternatives=_alternatives_for(constraints, merged),
            )

    return Infeasible(
        budget=MAX_TOTAL_POINTS,
        per_constraint_points=per_constraint,
        impossible=impossible,
        options=_maximal_options(chart, target, constraints, candidate_sets),
    )


def _maximal_options(
    chart: TypeChart,
    target: BuildTarget,
    constraints: Sequence[Constraint],
    candidate_sets: Sequence[tuple[_Candidate, ...]],
) -> tuple[Option, ...]:
    """만족 가능한 **최대** 부분집합들. 무엇을 얻고 무엇을 포기하는지 보여준다 (③번).

    부분집합이 또 다른 선택지에 포함되면 버린다 — 더 많이 만족하는 안이 있는데
    굳이 적게 만족하는 안을 보여줄 이유가 없다.
    """
    indices = range(len(constraints))
    found: list[tuple[frozenset[int], Option]] = []

    for size in range(len(constraints) - 1, 0, -1):
        for subset in combinations(indices, size):
            key = frozenset(subset)
            if any(key < existing for existing, _ in found):
                continue
            if any(not candidate_sets[i] for i in subset):
                continue
            best = _best_combination([candidate_sets[i] for i in subset])
            if best is None:
                continue
            merged, justifications, _ = best
            found.append(
                (
                    key,
                    Option(
                        satisfied=tuple(constraints[i].label for i in subset),
                        dropped=tuple(constraints[i].label for i in indices if i not in key),
                        spread=_spread_from(merged),
                        justifications=justifications,
                    ),
                )
            )
    return tuple(option for _, option in found)
