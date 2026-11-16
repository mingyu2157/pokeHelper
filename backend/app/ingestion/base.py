"""수집기 인터페이스 (CLAUDE.md 8-6).

**사이트별 구현을 서비스 계층에 노출하지 않는다.** 서비스는 `StatsSource` 만 알고,
어느 사이트에서 가져오는지 모른다. 출처가 바뀌어도 서비스 코드는 그대로다.

여기서 돌려주는 타입은 **이미 정규화된 내부 스키마**다. 사이트별 필드명
(`stat_alignment`, `percentage_value`, `hp_points` ...)이 이 경계를 넘어가지 않는다.
넘어가게 두면 DB 와 서비스 전체에 그 사이트의 어휘가 퍼지고, 두 번째 출처를 붙일 때
전부 고쳐야 한다.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class Attribution:
    """표시 의무가 있는 출처 표기 (CLAUDE.md 8-2).

    ★ 이것은 품질 항목이 아니라 **사용 조건**이다 ★

    championsbattledata 의 라이선스는 "데이터를 표시하는 모든 화면에 출처 표기와
    링크"를 요구한다. 지키지 않으면 서비스를 내려야 한다.

    가이드 4-7 은 `attribution: str` 하나로 적어뒀지만 **text 와 url 로 쪼갰다.**
    라이선스가 요구하는 것은 "표기"와 "링크" 두 개이고, 한 문자열로 합치면
    UI 가 다시 쪼개야 한다. 쪼개는 로직이 화면마다 생기면 거기서 틀린다.
    """

    text: str
    url: str


@dataclass(frozen=True)
class OptionUsage:
    """하나의 선택지 채용률. 기술·도구·특성·성격·파트너가 모두 이 모양이다.

    `rate` 가 None 인 경우가 있다 — 파트너(teammate)는 순위만 주고 비율을 주지 않는다.
    0.0 으로 채우지 않는다. "채용률이 0%" 와 "채용률을 모른다" 는 다르다.
    """

    option_id: str
    rank: int
    rate: float | None


@dataclass(frozen=True)
class SpreadUsage:
    """노력치 배분 채용률.

    ★ 챔피언스 표기다 — 스탯당 0~32, 총합 66 이하 (CLAUDE.md 5-0).
    252 EV 표기는 이 프로젝트에 존재하지 않는다. 출처가 252 로 주면
    수집 어댑터에서 포인트로 정규화한다. 서비스 안으로 252 가 새어 들어오지 않는다.
    """

    points: dict[str, int]
    rank: int
    rate: float | None


@dataclass(frozen=True)
class PokemonUsage:
    """포켓몬 하나의 메타 통계. 정규화가 끝난 형태다."""

    showdown_id: str
    rank: int | None
    usage_rate: float | None
    moves: tuple[OptionUsage, ...] = ()
    items: tuple[OptionUsage, ...] = ()
    abilities: tuple[OptionUsage, ...] = ()
    natures: tuple[OptionUsage, ...] = ()
    teammates: tuple[OptionUsage, ...] = ()
    spreads: tuple[SpreadUsage, ...] = ()
    # 수집 원본. 파싱을 고쳤을 때 재수집 없이 재파싱할 수 있다 (가이드 4-3).
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class CollectionResult:
    """한 번의 수집 결과 전체.

    `attribution` 에 **기본값을 두지 않는다.** 기본값이 있으면 두 번째 출처를 붙일 때
    출처 표기를 빼먹어도 코드가 돌아간다 — 라이선스 위반이 조용히 통과한다.
    """

    source: str
    attribution: Attribution
    format: str
    season: str
    collected_at: datetime
    entries: tuple[PokemonUsage, ...]


class StatsSource(ABC):
    """사용률 통계 출처."""

    @property
    @abstractmethod
    def name(self) -> str:
        """`meta_snapshots.source` 에 들어갈 식별자."""

    @property
    @abstractmethod
    def attribution(self) -> Attribution:
        """UI 에 표기할 출처 (CLAUDE.md 8-2).

        ★ 출처 표기를 인터페이스에 넣은 이유 (가이드 4-7) ★

        출처 표기가 UI 쪽 문자열 상수로만 있으면, 화면을 새로 만드는 사람이
        **깜빡할 수 있다.** 깜빡한 걸 알려주는 장치도 없다. 수집 결과와 함께
        흐르는 값으로 만들면 출처를 추가하면 표기도 자동으로 따라온다.
        """

    @abstractmethod
    async def collect(self, *, battle_format: str, limit: int) -> CollectionResult:
        """상위 `limit` 마리의 메타 통계를 가져온다.

        요청 간격과 동시성 제한은 **구현체가 지킨다** (CLAUDE.md 8-6).
        호출자가 신경 쓰게 만들면 언젠가 누군가 병렬로 돌린다.
        """
