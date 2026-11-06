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
from enum import Enum, auto
from itertools import combinations, product
from typing import Final

from app.domain.constants import (
    MAX_POINTS_PER_STAT,
    MAX_TOTAL_POINTS,
    MIN_POINTS_PER_STAT,
)
from app.domain.damage import calculate
from app.domain.effort import EffortSpread
from app.domain.exceptions import InvalidNatureSpread
from app.domain.indices import bulk_index
from app.domain.models import Attacker, BaseStats, Defender, MoveSpec
from app.domain.nature import NatureModifier, StatKey
from app.domain.stats import calc_hp, calc_stat, points_to_next_magic_number
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

    def __post_init__(self) -> None:
        """성격 보정 조합이 실제로 존재할 수 있는지 검증한다.

        - 상승·하락은 각각 한 스탯뿐이다
        - **항상 쌍이다** — 하나를 올리면 반드시 하나를 내린다 (무보정이면 둘 다 없다)
        - HP 는 보정을 받지 않는다

        두 스탯이 동시에 1.1배인 포켓몬은 존재하지 않는다. 막지 않으면
        불가능한 배분으로 계산한 수치가 사용자에게 나간다 (5-0 의 정신).
        """
        if StatKey.HP in self.natures:
            raise InvalidNatureSpread("HP 는 성격 보정을 받지 않는다 (CLAUDE.md 5-1).")
        rising = [s for s, m in self.natures.items() if m is NatureModifier.UP]
        falling = [s for s, m in self.natures.items() if m is NatureModifier.DOWN]
        if len(rising) > 1:
            raise InvalidNatureSpread(
                f"상승보정은 한 스탯에만 걸린다. 받은 스탯: {[s.name for s in rising]}"
            )
        if len(falling) > 1:
            raise InvalidNatureSpread(
                f"하락보정은 한 스탯에만 걸린다. 받은 스탯: {[s.name for s in falling]}"
            )
        if len(rising) != len(falling):
            raise InvalidNatureSpread(
                "성격 보정은 항상 쌍이다 — 하나를 올리면 반드시 하나를 내린다 "
                "(무보정 성격이면 둘 다 없다). "
                f"상승 {[s.name for s in rising]} / 하락 {[s.name for s in falling]}"
            )

    def modifier(self, stat: StatKey) -> NatureModifier:
        return self.natures.get(stat, NatureModifier.NEUTRAL)

    def actual(self, stat: StatKey, points: int) -> int:
        base = self.base_stats.of(stat)
        if stat is StatKey.HP:
            return calc_hp(base, points)
        return calc_stat(base, points, self.modifier(stat))


class AllocationPriority(Enum):
    """잔여 포인트를 어디에 부을 것인가 (규칙 4).

    5-4 는 "결정력 / 물리내구 / 특수내구"라고 적었는데, 결정력은 공격과 특공
    어느 쪽인지 모호하다. 그래서 물리/특수를 나눠 명시한다.
    """

    PHYSICAL_OFFENSE = auto()
    SPECIAL_OFFENSE = auto()
    PHYSICAL_BULK = auto()
    SPECIAL_BULK = auto()
    SPEED = auto()


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


_PRIORITY_LABELS: Final[Mapping[AllocationPriority, str]] = {
    AllocationPriority.PHYSICAL_OFFENSE: "물리 결정력",
    AllocationPriority.SPECIAL_OFFENSE: "특수 결정력",
    AllocationPriority.PHYSICAL_BULK: "물리내구",
    AllocationPriority.SPECIAL_BULK: "특수내구",
    AllocationPriority.SPEED: "스피드",
}

_PRIORITY_STATS: Final[Mapping[AllocationPriority, tuple[StatKey, ...]]] = {
    AllocationPriority.PHYSICAL_OFFENSE: (StatKey.ATTACK,),
    AllocationPriority.SPECIAL_OFFENSE: (StatKey.SP_ATTACK,),
    AllocationPriority.PHYSICAL_BULK: (StatKey.HP, StatKey.DEFENSE),
    AllocationPriority.SPECIAL_BULK: (StatKey.HP, StatKey.SP_DEFENSE),
    AllocationPriority.SPEED: (StatKey.SPEED,),
}


def _apply_magic_number(
    target: BuildTarget, points: dict[StatKey, int], budget: int
) -> tuple[int, Justification | None]:
    """상승보정 스탯을 매직넘버에 착지시킨다.

    성격 상승보정은 **한 스탯에만** 걸리므로 대상도 하나뿐이다.
    직전에서 멈추면 1포인트로 실수치 2를 얻을 기회를 버린다.
    """
    rising = [s for s, m in target.natures.items() if m is NatureModifier.UP]
    if not rising:
        return budget, None

    stat = rising[0]
    current = points.get(stat, 0)
    needed = points_to_next_magic_number(target.base_stats.of(stat), current, NatureModifier.UP)
    if not needed or needed > budget:
        return budget, None

    before = target.actual(stat, current)
    points[stat] = current + needed
    after = target.actual(stat, points[stat])
    detail = (
        f"매직넘버 착지 — {needed}포인트로 실수치 {before} → {after} "
        f"(+{after - before}, 버림 손실 0)"
    )
    return budget - needed, Justification(stat, needed, "매직넘버", detail)


def _allocate_bulk(
    target: BuildTarget,
    points: dict[StatKey, int],
    budget: int,
    defense_stat: StatKey,
    label: str,
) -> tuple[int, tuple[Justification, ...]]:
    """내구력(HP × 방어)을 최대화하는 배분. 실수치 합이 아니라 곱을 본다."""
    hp_floor = points.get(StatKey.HP, 0)
    def_floor = points.get(defense_stat, 0)

    def value_of(hp_points: int, def_points: int) -> float:
        return bulk_index(
            hp_stat=target.actual(StatKey.HP, hp_points),
            defense_stat=target.actual(defense_stat, def_points),
        )

    # 바닥값(아무것도 더 쓰지 않는 경우)에서 출발한다. 후보가 없는 경우는 생기지 않는다.
    best = (value_of(hp_floor, def_floor), hp_floor, def_floor)
    for hp_points in range(hp_floor, MAX_POINTS_PER_STAT + 1):
        spent_hp = hp_points - hp_floor
        if spent_hp > budget:
            break
        def_points = min(def_floor + budget - spent_hp, MAX_POINTS_PER_STAT)
        value = value_of(hp_points, def_points)
        if value > best[0]:
            best = (value, hp_points, def_points)

    _, hp_points, def_points = best
    spent = (hp_points - hp_floor) + (def_points - def_floor)
    points[StatKey.HP] = hp_points
    points[defense_stat] = def_points
    detail = (
        f"{label} 최대화 — HP {target.actual(StatKey.HP, hp_points)} × "
        f"방어 {target.actual(defense_stat, def_points)}"
    )
    justifications = tuple(
        Justification(stat, points[stat], label, detail)
        for stat in (StatKey.HP, defense_stat)
        if points[stat] > 0
    )
    return budget - spent, justifications


def _allocate_single(
    target: BuildTarget,
    points: dict[StatKey, int],
    budget: int,
    stat: StatKey,
    label: str,
) -> tuple[int, tuple[Justification, ...]]:
    current = points.get(stat, 0)
    added = min(budget, MAX_POINTS_PER_STAT - current)
    if added <= 0:
        return budget, ()
    points[stat] = current + added
    detail = f"{label} 우선 — 실수치 {target.actual(stat, points[stat])}"
    return budget - added, (Justification(stat, points[stat], label, detail),)


def _allocate_residual(
    target: BuildTarget,
    points: dict[StatKey, int],
    priority: AllocationPriority | None,
) -> tuple[dict[StatKey, int], tuple[Justification, ...]]:
    """제약을 만족하고 남은 포인트를 쓴다 (규칙 4).

    **우선순위를 주지 않으면 아무것도 더 쓰지 않는다.** 그 경우 호출자는
    "제약을 만족하는 최소 투자"를 기대하므로(5-4 규칙 2), 매직넘버 착지라도
    1포인트를 더 쓰면 약속이 깨진다.

    우선순위를 줬다면 매직넘버 착지를 **먼저** 본다 — 1포인트로 실수치 2를 얻는
    구간이라 우선순위를 거스를 만한 이득이고, 근거에 명시되면 납득할 수 있다.
    성격 상승보정은 한 스탯에만 걸리므로 대상도 하나뿐이다.
    """
    allocated = dict(points)
    if priority is None:
        return allocated, ()

    budget = MAX_TOTAL_POINTS - sum(allocated.values())
    extra: list[Justification] = []

    budget, magic = _apply_magic_number(target, allocated, budget)
    if magic is not None:
        extra.append(magic)

    if budget <= 0:
        return allocated, tuple(extra)

    label = _PRIORITY_LABELS[priority]
    stats = _PRIORITY_STATS[priority]
    if len(stats) == 2:
        budget, justifications = _allocate_bulk(target, allocated, budget, stats[1], label)
    else:
        budget, justifications = _allocate_single(target, allocated, budget, stats[0], label)
    extra.extend(justifications)
    return allocated, tuple(extra)


# ---------------------------------------------------------------- 공개 API


def solve(
    chart: TypeChart,
    target: BuildTarget,
    constraints: Sequence[Constraint],
    *,
    priority: AllocationPriority | None = None,
) -> SolverResult:
    """제약을 만족하는 최소 배분을 찾는다. 없으면 무엇이 충돌하는지 알린다.

    `priority` 를 주면 제약을 만족하고 남은 포인트까지 쓴다 (규칙 4).
    주지 않으면 필요한 만큼만 쓰고 남긴다.
    """
    if not constraints:
        allocated, extra = _allocate_residual(target, {}, priority)
        return Solution(spread=_spread_from(allocated), justifications=extra)

    candidate_sets = [_candidates_for(chart, target, c) for c in constraints]
    pairs = list(zip(constraints, candidate_sets, strict=True))

    impossible = tuple(c.label for c, candidates in pairs if not candidates)
    per_constraint = {c.label: _minimum_total(candidates) for c, candidates in pairs if candidates}

    if not impossible:
        best = _best_combination(candidate_sets)
        if best is not None:
            merged, justifications, _ = best
            allocated, extra = _allocate_residual(target, merged, priority)
            return Solution(
                spread=_spread_from(allocated),
                justifications=justifications + extra,
                alternatives=_alternatives_for(constraints, allocated),
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
