"""메타 현황 API 통합 테스트.

**실제 Postgres 를 쓰되, 데이터는 이 테스트가 직접 만든다.**

★ 처음엔 개발 DB 에 이미 있던 활성 스냅샷에 기댔고, CI 에서 13개가 깨졌다 ★
CI 는 깨끗한 DB 에 시드만 적재한다. 수집은 돌지 않는다 — 돌면 안 된다
(외부 API 를 CI 에서 부르지 않는다). 그래서 활성 스냅샷이 없고 전부 404 였다.
"내 머신에서 통과"는 통과가 아니다 (JOURNAL 2026-11-18).

스냅샷은 **커밋하지 않는다.** 의존성을 오버라이드해 API 가 테스트와 같은
세션을 쓰게 하고, 끝나면 롤백한다. 커밋하면 개발 DB 의 활성 스냅샷과
부분 유니크 인덱스(`uq_meta_snapshot_active`)에서 충돌하고, 남으면 화면이
테스트 데이터를 보여준다.

★ 이 파일의 절반은 기능이 아니라 **라이선스 조건**을 지킨다 ★
출처 표기 필수, 덤프 금지, 미매핑 표시 — 셋 다 깨져도 화면에는 숫자가
멀쩡히 나온다. 눈에 띄지 않는 위반이라 테스트가 막아야 한다.

`db` 마커가 붙어 있다.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, date, datetime

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.db.models import Regulation
from app.db.session import get_session
from app.ingestion import attribution as attribution_registry
from app.ingestion.base import (
    Attribution,
    CollectionResult,
    OptionUsage,
    PokemonUsage,
    SpreadUsage,
)
from app.main import app
from app.schemas.meta import MAX_ENTRY_LIMIT, SnapshotOut, UsageListOut
from app.services.ingest import ingest

pytestmark = pytest.mark.db

#: 시드에 존재하는 Showdown ID 들. FK(`pokemon.showdown_id`)가 RESTRICT 라
#: 없는 id 를 쓰면 적재 자체가 실패한다.
SEEDED_POKEMON = ("garchomp", "primarina", "hippowdon", "dragonite")

#: ★ 한글명이 없는 도구 ★ 도구 한영 매핑은 Phase 2-3c 로 미뤄져 있어서
#: 89종 전부 미매핑이다. 미매핑 표시 테스트가 이 사실에 기댄다.
UNMAPPED_ITEM = "choicescarf"


def _entry(showdown_id: str, rank: int) -> PokemonUsage:
    """채용률이 전부 달린 엔트리. JSONB 모양을 손으로 만들지 않는다.

    `ingest()` 를 통과시켜야 저장되는 `data` 구조가 **운영과 같아진다.**
    직접 INSERT 하면 그 구조가 테스트에만 있는 모양으로 굳고, 수집 쪽이
    바뀌어도 이 테스트는 모른다.
    """
    return PokemonUsage(
        showdown_id=showdown_id,
        rank=rank,
        # 출처가 사용률 %를 주지 않는다. 0 으로 채우지 않는다.
        usage_rate=None,
        moves=(OptionUsage("earthquake", 1, 75.5), OptionUsage("stealthrock", 2, 38.2)),
        items=(OptionUsage(UNMAPPED_ITEM, 1, 22.4),),
        abilities=(OptionUsage("roughskin", 1, 99.3),),
        natures=(OptionUsage("jolly", 1, 31.0),),
        # 파트너는 순위만 주고 비율을 주지 않는다 (rate=None).
        teammates=(OptionUsage("dragonite", 1, None),),
        spreads=(
            SpreadUsage(
                {"hp": 2, "attack": 32, "defense": 0, "sp_attack": 0, "sp_defense": 0, "speed": 32},
                1,
                34.3,
            ),
            SpreadUsage(
                {"hp": 32, "attack": 0, "defense": 32, "sp_attack": 0, "sp_defense": 2, "speed": 0},
                2,
                17.4,
            ),
        ),
        raw={"rows": ["이 원본은 응답에 절대 나오면 안 된다"]},
    )


@pytest.fixture
async def session() -> AsyncIterator[AsyncSession]:
    """테스트마다 롤백한다. 커밋하지 않는다."""
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    try:
        async with factory() as session:
            yield session
            await session.rollback()
    finally:
        await engine.dispose()


@pytest.fixture
async def client(session: AsyncSession) -> AsyncIterator[AsyncClient]:
    """픽스처 스냅샷을 만들고, API 가 **같은 세션**을 쓰게 한다.

    의존성을 오버라이드하지 않으면 API 는 자기 세션을 열고, 커밋하지 않은
    스냅샷을 보지 못한다. 그렇다고 커밋하면 개발 DB 에 테스트 데이터가 남는다.
    """
    regulation = Regulation(
        code=f"META-TEST-{datetime.now(UTC).timestamp()}",
        name="메타 API 테스트",
        started_at=date(2026, 1, 1),
        rules={},
    )
    session.add(regulation)
    await session.flush()

    result = CollectionResult(
        source="championsbattledata",
        attribution=Attribution(
            text="Battle data provided by Pokémon Champions Battle Data",
            url="https://championsbattledata.com",
        ),
        format="Singles",
        season="Current",
        # 개발 DB 에 실제 스냅샷이 있어도 **이쪽이 더 최신**이어야 저장소가
        # 이걸 고른다 (가장 최근 수집분을 활성으로 본다).
        collected_at=datetime.now(UTC),
        entries=tuple(
            _entry(showdown_id, rank) for rank, showdown_id in enumerate(SEEDED_POKEMON, start=1)
        ),
    )
    report = await ingest(session, result=result, regulation_id=regulation.id)
    assert report.activated, f"픽스처 스냅샷이 활성화되지 않았다: {report.unresolved}"

    async def override() -> AsyncIterator[AsyncSession]:
        yield session

    app.dependency_overrides[get_session] = override
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as http:
            yield http
    finally:
        app.dependency_overrides.clear()


@pytest.fixture
async def usage(client: AsyncClient) -> dict:
    response = await client.get("/api/v1/meta/usage", params={"limit": 10})
    assert response.status_code == 200, response.text
    return response.json()


@pytest.fixture
async def detail(client: AsyncClient, usage: dict) -> dict:
    pokemon_id = usage["entries"][0]["pokemon_id"]
    response = await client.get(f"/api/v1/meta/pokemon/{pokemon_id}")
    assert response.status_code == 200, response.text
    return response.json()


class TestUsageList:
    """기능 검증 — 순위표가 순위대로, 쓸 수 있는 모양으로 나오는가."""

    async def test_entries_are_ranked_in_order(self, usage: dict) -> None:
        ranks = [entry["rank"] for entry in usage["entries"]]
        assert ranks == sorted(r for r in ranks if r is not None)

    async def test_limit_is_respected(self, client: AsyncClient) -> None:
        response = await client.get("/api/v1/meta/usage", params={"limit": 3})
        assert len(response.json()["entries"]) == 3

    async def test_korean_names_are_resolved(self, usage: dict) -> None:
        """포켓몬 한영 매핑은 커버리지 100% 다. 하나라도 비면 매핑이 깨진 것이다."""
        missing = [e["pokemon_id"] for e in usage["entries"] if not e["name_ko"]]
        assert not missing, f"한글명이 없는 포켓몬: {missing}"

    async def test_collected_at_is_present(self, usage: dict) -> None:
        """수집 시각 없이 통계를 띄우지 않는다 (CLAUDE.md 1-2).

        숫자만 있으면 사용자는 그게 오늘 것인지 2주 전 것인지 알 수 없다.
        """
        assert usage["snapshot"]["collected_at"]
        assert usage["snapshot"]["regulation"]


class TestUsageRateStaysUnknown:
    """차등 단정 — "모른다"를 0 으로 바꾸면 이 테스트만 깨진다.

    출처 API 가 사용률 퍼센트를 주지 않는다. 0.0 으로 채우면 화면에
    "사용률 0%" 가 뜨고, 그건 **틀린 사실**이다. 수집 쪽에도 같은 단정이
    있고(`test_ingest.py::test_usage_rate_stays_null`), 여기는 그것이
    **표시 경계까지** 유지되는지 본다.
    """

    async def test_usage_rate_is_null_not_zero(self, usage: dict) -> None:
        values = [entry["usage_rate"] for entry in usage["entries"]]
        assert values, "엔트리가 비어 있어 아무것도 검증하지 못했다"
        assert all(value is None for value in values), f"0 으로 채워진 값이 있다: {values}"


class TestAttributionIsMandatory:
    """★ 라이선스 조건 — 출처 표기 없이는 응답이 만들어지지 않는다.

    표기 누락은 **조용한 위반**이다. 화면에는 숫자가 멀쩡히 나오고 출처 줄만
    비어 있다. 그래서 스키마가 기본값을 주지 않고, 그 사실을 테스트가 고정한다.
    수집 쪽 `CollectionResult.attribution` 과 같은 장치다 (ADR-013).
    """

    async def test_usage_response_carries_attribution(self, usage: dict) -> None:
        assert usage["attributions"], "출처 표기가 비어 있다"
        for item in usage["attributions"]:
            assert item["text"] and item["url"], "표기와 링크는 쌍으로 나가야 한다"

    async def test_detail_response_carries_attribution(self, detail: dict) -> None:
        assert detail["attributions"]
        for item in detail["attributions"]:
            assert item["text"] and item["url"]

    async def test_attribution_text_is_the_required_wording(self, usage: dict) -> None:
        """문구를 임의로 바꾸지 않는다. 라이선스가 요구하는 표현이다."""
        texts = [item["text"] for item in usage["attributions"]]
        assert attribution_registry.CHAMPIONSBATTLEDATA.text in texts

    def test_schema_refuses_a_response_without_attribution(self) -> None:
        """★ 전제 — 위 단정들이 "우연히 채워져서" 통과하는 게 아니다.

        스키마에 기본값이 있으면 표기를 빼먹어도 응답이 만들어지고, 위
        테스트들은 그걸 못 잡는다. 기본값이 없다는 사실을 직접 단정한다.
        """
        with pytest.raises(ValidationError):
            UsageListOut(
                snapshot=SnapshotOut(
                    source="championsbattledata",
                    format="Singles",
                    regulation="M7",
                    collected_at="2026-11-14T12:56:03Z",  # type: ignore[arg-type]
                ),
                entries=[],
            )  # type: ignore[call-arg]

    def test_unknown_source_fails_closed(self) -> None:
        """모르는 출처는 빈 표기가 아니라 예외다.

        두 번째 출처를 붙이는 사람이 등록을 빠뜨리면, 그 데이터가 **처음
        화면에 나가려는 순간** 실패한다. 표기 없이 배포되는 것보다 낫다.
        """
        with pytest.raises(attribution_registry.UnknownSourceError):
            attribution_registry.for_source("some-new-site")

    def test_every_known_source_has_text_and_url(self) -> None:
        for source in attribution_registry.known_sources():
            entry = attribution_registry.for_source(source)
            assert entry.text and entry.url, f"{source}: 표기나 링크가 비었다"


class TestNotADump:
    """★ 라이선스 조건 — 우리 API 가 출처의 덤프가 되지 않는다.

    "경쟁 API·덤프·대량 다운로드 서비스 금지"가 조건이다 (CLAUDE.md 8-2).
    우리 API 는 분석 결과를 돌려주고, 원본 통계는 **화면에 필요한 만큼만** 싣는다.
    """

    async def test_limit_above_the_cap_is_rejected(self, client: AsyncClient) -> None:
        response = await client.get("/api/v1/meta/usage", params={"limit": MAX_ENTRY_LIMIT + 1})
        assert response.status_code == 422, "상한 없는 limit 은 이 API 를 덤프로 만든다"

    async def test_limit_at_the_cap_is_allowed(self, client: AsyncClient) -> None:
        """경계값 — 상한 자체는 유효해야 한다. 상한을 금지선과 혼동하지 않는다."""
        response = await client.get("/api/v1/meta/usage", params={"limit": MAX_ENTRY_LIMIT})
        assert response.status_code == 200

    async def test_raw_collection_payload_is_never_exposed(self, detail: dict, usage: dict) -> None:
        """수집 원본(`pokemon_meta.raw`)이 응답 어디에도 없다.

        저장소가 아예 SELECT 하지 않지만, 누가 편의로 모델 전체를 직렬화하면
        조용히 새어 나간다. 바깥 경계에서 다시 본다.
        """
        for payload in (detail, usage):
            assert "raw" not in _all_keys(payload), "수집 원본이 응답에 섞였다"


class TestUnmappedNamesAreMarked:
    """★ 매핑 실패를 통과시키지 않는다 (CLAUDE.md 8-4) 의 표시 쪽 대응.

    지금 도구는 한글명이 전부 비어 있다 (Phase 2-3c 미완). 영문 id 를
    한글명 자리에 넣으면 사용자는 그게 **우리가 고른 표기**라고 읽는다.
    그래서 `name_ko` 는 null 로 두고, 미매핑 개수를 함께 돌려준다.
    """

    async def test_unmapped_options_have_null_name(self, detail: dict) -> None:
        for category, options in detail["options"].items():
            unmapped = detail["unmapped"][category]
            actual = sum(1 for option in options if option["name_ko"] is None)
            assert actual == unmapped, f"{category}: 미매핑 개수가 실제와 다르다"

    async def test_english_id_never_lands_in_the_korean_name(self, detail: dict) -> None:
        """영문 id 가 한글명 자리에 들어가지 않았다."""
        for options in detail["options"].values():
            for option in options:
                assert option["name_ko"] != option["option_id"]

    async def test_the_check_is_not_vacuous(self, detail: dict) -> None:
        """전제 선행 단정 — 미매핑이 **실제로 있었다**.

        전부 매핑돼 있으면 위 두 단정이 공짜로 통과한다 (TEST_STRATEGY 3-9).
        도구 매핑이 끝나면 이 단정이 깨지고, 그때 이 클래스를 손봐야 한다.
        """
        assert detail["unmapped"]["items"] > 0, (
            f"'{UNMAPPED_ITEM}' 이 매핑됐다. 도구 매핑이 끝났다면 이 테스트를 갱신한다"
        )


class TestOptionRates:
    """채용률도 "모른다"를 0 으로 바꾸지 않는다."""

    async def test_teammate_rate_stays_null(self, detail: dict) -> None:
        """파트너는 순위만 주고 비율을 주지 않는다. 0.0 으로 채우지 않는다."""
        rates = [option["rate"] for option in detail["options"]["teammates"]]
        assert rates, "파트너가 비어 있어 아무것도 검증하지 못했다"
        assert all(rate is None for rate in rates), rates

    async def test_move_rates_are_present(self, detail: dict) -> None:
        """반대로 기술은 비율을 준다 — 위 테스트가 '전부 null' 로 공짜 통과하지 않는다."""
        rates = [option["rate"] for option in detail["options"]["moves"]]
        assert rates and all(rate is not None for rate in rates), rates


class TestSpreadNotation:
    """노력치 배분은 챔피언스 표기로 나간다 (CLAUDE.md 5-0, 4장)."""

    async def test_notation_follows_habcds_order(self, detail: dict) -> None:
        order = "HABCDS"
        assert detail["spreads"], "배분이 비어 있어 아무것도 검증하지 못했다"
        for spread in detail["spreads"]:
            letters = [c for c in spread["notation"] if c in order]
            assert letters == sorted(letters, key=order.index), spread["notation"]

    async def test_points_respect_champions_limits(self, detail: dict) -> None:
        """스탯당 32, 총합 66. 기존 시리즈 표기는 이 프로젝트에 없다."""
        for spread in detail["spreads"]:
            points = spread["points"]
            assert all(0 <= value <= 32 for value in points.values()), points
            assert sum(points.values()) <= 66, points


class TestMissingData:
    """부정 테스트 — 없는 것을 물으면 404 다. 500 이 아니다."""

    async def test_unknown_pokemon_is_404(self, client: AsyncClient) -> None:
        response = await client.get("/api/v1/meta/pokemon/notapokemon")
        assert response.status_code == 404

    async def test_unknown_format_is_rejected_by_schema(self, client: AsyncClient) -> None:
        response = await client.get("/api/v1/meta/usage", params={"format": "Triples"})
        assert response.status_code == 422

    async def test_no_active_snapshot_is_404_not_500(self, client: AsyncClient) -> None:
        """활성 스냅샷이 없는 포맷은 404 다.

        ★ CI 를 깨뜨린 상황이 바로 이것이다 ★ 수집이 한 번도 안 돈 환경에서는
        모든 포맷이 이 상태다. 오류가 아니라 **상태**이고, 계산 기능은 이것과
        무관하게 동작해야 한다 (CLAUDE.md 8-5).
        """
        response = await client.get("/api/v1/meta/usage", params={"format": "Doubles"})
        assert response.status_code == 404
        assert "활성 스냅샷이 없다" in response.json()["detail"]


def _all_keys(payload: object) -> set[str]:
    """중첩 구조 전체의 키를 모은다."""
    keys: set[str] = set()
    if isinstance(payload, dict):
        for key, value in payload.items():
            keys.add(key)
            keys |= _all_keys(value)
    elif isinstance(payload, list):
        for item in payload:
            keys |= _all_keys(item)
    return keys
