"""championsbattledata 어댑터 단위 테스트 (Phase 2-2, CLAUDE.md 8-1/8-2/8-6).

**실제 API 를 호출하지 않는다.** `tests/fixtures/championsbattledata/` 의 샘플
응답을 가짜 전송계층으로 돌려준다. 그 샘플이 실측이 아니라 '우리가 가정하는 계약'
이라는 한계는 해당 디렉터리의 README 에 적어뒀다 — 이 테스트는 회귀를 막지만
출처가 형태를 바꾼 것은 탐지하지 못한다.

★ 여기서 지키는 것 세 가지 ★
1. 사이트 어휘(`showdownId`, `percentage_value`, `stat_alignment`)가 경계를 넘지 않는다
2. 출처 표기가 데이터와 함께 흐른다 (라이선스 사용 조건, CLAUDE.md 8-2)
3. 모든 요청이 예절 게이트를 지나간다 (CLAUDE.md 8-6)
"""

from __future__ import annotations

import json
import pathlib

import httpx
import pytest

from app.ingestion.base import Attribution, CollectionResult
from app.ingestion.championsbattledata import (
    TRANSIENT_RETRIES,
    ChampionsBattleDataSource,
    CollectionError,
)
from app.ingestion.gate import RequestGate

FIXTURES = pathlib.Path(__file__).resolve().parents[2] / "fixtures" / "championsbattledata"


def _fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


class RecordingSleep:
    def __init__(self, clock: FakeClock) -> None:
        self.clock = clock
        self.waits: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.waits.append(seconds)
        self.clock.now += seconds


class Recorder:
    """가짜 전송계층. 요청 순서를 기록한다."""

    def __init__(self, *, status: int = 200, raise_transport: bool = False) -> None:
        self.paths: list[str] = []
        self._status = status
        self._raise_transport = raise_transport

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.paths.append(request.url.path)
        if self._raise_transport:
            raise httpx.ConnectError("연결 실패", request=request)
        if self._status != 200:
            return httpx.Response(self._status, json={"error": "nope"})

        path = request.url.path
        if path == "/api/rankings":
            return httpx.Response(200, json=_fixture("rankings.json"))
        if path.startswith("/api/battle/Singles/"):
            showdown_id = path.rsplit("/", 1)[-1]
            return httpx.Response(200, json=_fixture(f"battle_singles_{showdown_id}.json"))
        return httpx.Response(404, json={"error": "unknown path"})


def _source(recorder: Recorder) -> tuple[ChampionsBattleDataSource, RecordingSleep]:
    clock = FakeClock()
    sleep = RecordingSleep(clock)
    client = httpx.AsyncClient(transport=httpx.MockTransport(recorder))
    gate = RequestGate(min_interval=1.0, clock=clock, sleep=sleep)
    return ChampionsBattleDataSource(client=client, gate=gate), sleep


async def _collect(recorder: Recorder, *, limit: int = 2) -> CollectionResult:
    source, _sleep = _source(recorder)
    return await source.collect(battle_format="Singles", limit=limit)


class TestParsingContract:
    """fixture 를 기대값으로 고정한다 — 파서를 고쳤을 때 계약이 바뀌면 깨진다.

    이 검증이 막는 것: 사이트별 필드명이나 그 해석이 조용히 바뀌어
    DB 에 다른 뜻의 값이 들어가는 것.
    """

    async def test_entries_follow_the_ranking_order(self):
        result = await _collect(Recorder())
        assert [entry.showdown_id for entry in result.entries] == ["garchomp", "raichualola"]

    async def test_rank_comes_from_position(self):
        result = await _collect(Recorder())
        assert [entry.rank for entry in result.entries] == [1, 2]

    async def test_usage_rate_stays_unknown(self):
        """★ 이 API 는 포켓몬 사용률 %를 주지 않는다.

        0.0 으로 채우면 '아무도 안 쓴다'는 거짓이 된다. None 이어야 한다.
        """
        result = await _collect(Recorder())
        assert all(entry.usage_rate is None for entry in result.entries)

    async def test_display_names_become_ids(self):
        """'Rough Skin' 이 'roughskin' 이 된다. 마스터와는 id 로 조인한다 (ADR-005)."""
        result = await _collect(Recorder())
        garchomp = result.entries[0]
        assert [option.option_id for option in garchomp.abilities] == ["roughskin"]
        assert [option.option_id for option in garchomp.moves] == ["earthquake", "dragonclaw"]
        assert [option.option_id for option in garchomp.items] == ["choicescarf"]
        assert [option.option_id for option in garchomp.natures] == ["jolly"]

    async def test_teammate_rate_stays_unknown(self):
        """파트너는 순위만 주고 비율을 주지 않는다."""
        result = await _collect(Recorder())
        assert result.entries[0].teammates[0].rate is None

    async def test_spread_is_champions_points(self):
        """★ 총합 66 — 챔피언스 상한 그 자체다 (CLAUDE.md 5-0).

        252 EV 표기가 경계를 넘어오면 여기서 깨진다.
        """
        result = await _collect(Recorder())
        spread = result.entries[0].spreads[0]
        assert spread.points == {
            "hp": 2,
            "attack": 32,
            "defense": 0,
            "sp_attack": 0,
            "sp_defense": 0,
            "speed": 32,
        }
        assert sum(spread.points.values()) == 66

    async def test_season_alias_is_resolved(self):
        """fixture 의 season 은 Current 다. 그대로 쓰면 시즌이 섞인다 (ADR-011)."""
        result = await _collect(Recorder())
        assert result.season == "M7"

    async def test_raw_is_preserved_for_reparsing(self):
        result = await _collect(Recorder())
        assert result.entries[0].raw["rows"]

    async def test_site_vocabulary_does_not_cross_the_boundary(self):
        """★ 정규화된 필드에 사이트 어휘가 남아 있지 않다.

        `raw` 는 의도적으로 원본이므로 검사에서 제외한다.
        """
        result = await _collect(Recorder())
        entry = result.entries[0]
        leaked = [
            option.option_id
            for bucket in (entry.moves, entry.items, entry.abilities, entry.natures)
            for option in bucket
            if not option.option_id.isalnum()
        ]
        assert leaked == []
        assert set(entry.spreads[0].points) == {
            "hp",
            "attack",
            "defense",
            "sp_attack",
            "sp_defense",
            "speed",
        }


class TestAttribution:
    """출처 표기는 품질 항목이 아니라 **사용 조건**이다 (CLAUDE.md 8-2).

    이 검증이 막는 것: 출처 표기가 UI 문구로만 존재해서, 새 출처를 붙이거나
    화면을 새로 만들 때 조용히 빠지는 것. 데이터와 함께 흐르면 빠뜨릴 수 없다.
    """

    async def test_source_declares_attribution(self):
        source, _sleep = _source(Recorder())
        assert isinstance(source.attribution, Attribution)

    async def test_result_carries_attribution(self):
        """★ 가이드 4-7 의 요점. 수집 결과를 들고 다니면 출처가 따라온다."""
        result = await _collect(Recorder())
        assert result.attribution == ChampionsBattleDataSource().attribution

    async def test_required_wording_is_pinned(self):
        """변경 탐지기 — 라이선스가 요구하는 문구를 바꾸면 깨진다."""
        source, _sleep = _source(Recorder())
        assert source.attribution.text == "Battle data provided by Pokémon Champions Battle Data"
        assert source.attribution.url == "https://championsbattledata.com"

    def test_attribution_is_not_optional(self):
        """기본값이 있으면 새 출처가 출처 표기를 빼먹을 수 있다."""
        with pytest.raises(TypeError):
            CollectionResult(  # type: ignore[call-arg]
                source="x",
                format="Singles",
                season="M7",
                collected_at=None,  # type: ignore[arg-type]
                entries=(),
            )


class TestRequestPoliteness:
    """모든 요청이 게이트를 지나가는지 (CLAUDE.md 8-6, 가이드 4-4 체크리스트).

    이 검증이 막는 것: 간격을 '포켓몬 루프 안에서만' 지켜서 목록 요청과 첫 상세
    요청 사이가 0 초가 되는 것. 상수 선언과 실제 호출 경로는 별개다.
    """

    async def test_every_request_after_the_first_waits(self):
        """요청 3회(목록 1 + 상세 2)면 대기는 2회여야 한다."""
        recorder = Recorder()
        source, sleep = _source(recorder)
        await source.collect(battle_format="Singles", limit=2)
        assert len(recorder.paths) == 3
        assert sleep.waits == [pytest.approx(1.0), pytest.approx(1.0)]

    async def test_wait_count_is_one_less_than_request_count(self):
        """첫 요청 앞에는 기다리지 않는다."""
        recorder = Recorder()
        source, sleep = _source(recorder)
        await source.collect(battle_format="Singles", limit=2)
        assert len(sleep.waits) == len(recorder.paths) - 1

    async def test_the_list_request_comes_first(self):
        recorder = Recorder()
        await _collect(recorder)
        assert recorder.paths[0] == "/api/rankings"


class TestConfiguredPolicy:
    """변경 탐지기 — 선언한 요청 예절 수치를 바꾸면 깨진다 (CLAUDE.md 8-6).

    이 검증이 막는 것: 수집을 빠르게 하려고 간격을 줄이는 변경이 조용히 들어오는 것.
    출처에는 고정 rate limit 이 없으므로 아무도 대신 막아주지 않는다.
    """

    def test_default_gate_enforces_one_second(self):
        """상수가 1.0 인 것과 **게이트가 그 값을 쓰는 것**은 별개다."""
        assert ChampionsBattleDataSource().gate.min_interval == pytest.approx(1.0)


class TestFailurePaths:
    """오류 추정 — 실패를 성공으로 끝내면 스케줄러가 초록으로 뜬다 (ADR-011)."""

    async def test_http_status_error_is_not_retried(self):
        """★ 데이터가 틀린 것과 네트워크가 끊긴 것은 다르다.

        500 을 재시도하면 출처를 세 배로 때리고 결과는 같다.
        """
        recorder = Recorder(status=500)
        source, _sleep = _source(recorder)
        with pytest.raises(CollectionError):
            await source.collect(battle_format="Singles", limit=2)
        assert len(recorder.paths) == 1

    async def test_transport_error_is_retried(self):
        recorder = Recorder(raise_transport=True)
        source, _sleep = _source(recorder)
        with pytest.raises(CollectionError):
            await source.collect(battle_format="Singles", limit=2)
        assert len(recorder.paths) == TRANSIENT_RETRIES

    async def test_limit_below_one_is_rejected(self):
        """경계값 — limit 0 은 아무것도 수집하지 않은 것을 성공으로 만든다."""
        source, _sleep = _source(Recorder())
        with pytest.raises(CollectionError):
            await source.collect(battle_format="Singles", limit=0)
