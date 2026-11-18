"""메타 현황 API 통합 테스트.

**실제 Postgres 와 개발 DB 의 활성 스냅샷을 쓴다.** 이 엔드포인트가 하는 일이
"활성 스냅샷을 읽어 표시용으로 바꾸는 것"이라, 스냅샷을 가짜로 만들면
정작 검증하려는 조인(한영 매핑)과 JSONB 모양이 전부 가짜가 된다.

★ 이 파일의 절반은 기능이 아니라 **라이선스 조건**을 지킨다 ★
출처 표기 필수, 덤프 금지, 미매핑 표시 — 셋 다 깨져도 화면에는 숫자가
멀쩡히 나온다. 눈에 띄지 않는 위반이라 테스트가 막아야 한다.

`db` 마커가 붙어 있다.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.db.session import get_engine, get_session_factory
from app.ingestion import attribution as attribution_registry
from app.main import app
from app.schemas.meta import MAX_ENTRY_LIMIT, SnapshotOut, UsageListOut

pytestmark = pytest.mark.db


@pytest.fixture(scope="module")
def client():
    """★ 이벤트 루프와 커넥션 풀을 이 모듈에 묶는다 ★

    두 가지가 겹쳐서 `RuntimeError: Event loop is closed` 가 난다.

    1. `TestClient(app)` 를 `with` 없이 쓰면 **요청마다 새 이벤트 루프**가
       돈다. 앞 요청이 만든 비동기 커넥션은 이미 닫힌 루프에 묶여 있다.
    2. 엔진은 `lru_cache` 로 **프로세스당 하나**다 (운영에서는 옳다).
       그래서 앞선 테스트 모듈이 자기 루프에서 엔진을 먼저 쓰면, 풀에 남은
       커넥션이 그 **닫힌 루프**에 묶인 채 이 모듈로 넘어온다.
       이 모듈만 돌리면 통과하고 전체 실행에서만 깨지는 이유다.

    `with` 로 루프를 하나로 묶고, 캐시를 비워 **이 모듈의 루프에서** 엔진을
    새로 만든다. 운영 코드는 건드리지 않는다 — 운영에는 루프가 하나뿐이라
    이 문제가 없고, 테스트 격리를 위해 운영 구조를 바꾸는 건 거꾸로다.
    """
    get_engine.cache_clear()
    get_session_factory.cache_clear()
    with TestClient(app) as client:
        yield client
    # 다음 모듈도 자기 루프에서 새로 만들게 둔다.
    get_engine.cache_clear()
    get_session_factory.cache_clear()


@pytest.fixture(scope="module")
def usage(client: TestClient) -> dict:
    response = client.get("/api/v1/meta/usage", params={"limit": 10})
    assert response.status_code == 200, response.text
    return response.json()


@pytest.fixture(scope="module")
def detail(client: TestClient, usage: dict) -> dict:
    pokemon_id = usage["entries"][0]["pokemon_id"]
    response = client.get(f"/api/v1/meta/pokemon/{pokemon_id}")
    assert response.status_code == 200, response.text
    return response.json()


class TestUsageList:
    """기능 검증 — 순위표가 순위대로, 쓸 수 있는 모양으로 나오는가."""

    def test_entries_are_ranked_in_order(self, usage: dict) -> None:
        ranks = [entry["rank"] for entry in usage["entries"]]
        assert ranks == sorted(r for r in ranks if r is not None)

    def test_limit_is_respected(self, client: TestClient) -> None:
        response = client.get("/api/v1/meta/usage", params={"limit": 3})
        assert len(response.json()["entries"]) == 3

    def test_korean_names_are_resolved(self, usage: dict) -> None:
        """포켓몬 한영 매핑은 커버리지 100% 다. 하나라도 비면 매핑이 깨진 것이다."""
        missing = [e["pokemon_id"] for e in usage["entries"] if not e["name_ko"]]
        assert not missing, f"한글명이 없는 포켓몬: {missing}"

    def test_collected_at_is_present(self, usage: dict) -> None:
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

    def test_usage_rate_is_null_not_zero(self, usage: dict) -> None:
        values = [entry["usage_rate"] for entry in usage["entries"]]
        assert values, "엔트리가 비어 있어 아무것도 검증하지 못했다"
        assert all(value is None for value in values), f"0 으로 채워진 값이 있다: {values}"


class TestAttributionIsMandatory:
    """★ 라이선스 조건 — 출처 표기 없이는 응답이 만들어지지 않는다.

    표기 누락은 **조용한 위반**이다. 화면에는 숫자가 멀쩡히 나오고 출처 줄만
    비어 있다. 그래서 스키마가 기본값을 주지 않고, 그 사실을 테스트가 고정한다.
    수집 쪽 `CollectionResult.attribution` 과 같은 장치다 (ADR-013).
    """

    def test_usage_response_carries_attribution(self, usage: dict) -> None:
        assert usage["attributions"], "출처 표기가 비어 있다"
        for item in usage["attributions"]:
            assert item["text"] and item["url"], "표기와 링크는 쌍으로 나가야 한다"

    def test_detail_response_carries_attribution(self, detail: dict) -> None:
        assert detail["attributions"]
        for item in detail["attributions"]:
            assert item["text"] and item["url"]

    def test_attribution_text_is_the_required_wording(self, usage: dict) -> None:
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

    def test_limit_above_the_cap_is_rejected(self, client: TestClient) -> None:
        response = client.get("/api/v1/meta/usage", params={"limit": MAX_ENTRY_LIMIT + 1})
        assert response.status_code == 422, "상한 없는 limit 은 이 API 를 덤프로 만든다"

    def test_limit_at_the_cap_is_allowed(self, client: TestClient) -> None:
        """경계값 — 상한 자체는 유효해야 한다. 상한을 금지선과 혼동하지 않는다."""
        response = client.get("/api/v1/meta/usage", params={"limit": MAX_ENTRY_LIMIT})
        assert response.status_code == 200

    def test_raw_collection_payload_is_never_exposed(self, detail: dict, usage: dict) -> None:
        """수집 원본(`pokemon_meta.raw`)이 응답 어디에도 없다.

        저장소가 아예 SELECT 하지 않지만, 누가 편의로 모델 전체를 직렬화하면
        조용히 새어 나간다. 바깥 경계에서 다시 본다.
        """
        for payload in (detail, usage):
            assert "raw" not in _all_keys(payload), "수집 원본이 응답에 섞였다"


class TestUnmappedNamesAreMarked:
    """★ 매핑 실패를 통과시키지 않는다 (CLAUDE.md 8-4) 의 표시 쪽 대응.

    지금 도구 89종이 전부 한글명 미매핑이다 (Phase 2-3c 미완). 영문 id 를
    한글명 자리에 넣으면 사용자는 그게 **우리가 고른 표기**라고 읽는다.
    그래서 `name_ko` 는 null 로 두고, 미매핑 개수를 함께 돌려준다.
    """

    def test_unmapped_options_have_null_name(self, detail: dict) -> None:
        for category, options in detail["options"].items():
            unmapped = detail["unmapped"][category]
            actual = sum(1 for option in options if option["name_ko"] is None)
            assert actual == unmapped, f"{category}: 미매핑 개수가 실제와 다르다"

    def test_english_id_never_lands_in_the_korean_name(self, detail: dict) -> None:
        """영문 id 가 한글명 자리에 들어가지 않았다."""
        for options in detail["options"].values():
            for option in options:
                assert option["name_ko"] != option["option_id"]

    def test_the_check_is_not_vacuous(self, detail: dict) -> None:
        """전제 선행 단정 — 미매핑이 **실제로 있었다**.

        전부 매핑돼 있으면 위 두 단정이 공짜로 통과한다 (TEST_STRATEGY 3-9).
        도구 매핑이 끝나면 이 단정이 깨지고, 그때 이 클래스를 손봐야 한다.
        """
        assert sum(detail["unmapped"].values()) > 0, (
            "미매핑이 하나도 없다. 도구 매핑이 끝났다면 이 테스트를 갱신한다"
        )


class TestSpreadNotation:
    """노력치 배분은 챔피언스 표기로 나간다 (CLAUDE.md 5-0, 4장)."""

    def test_notation_follows_habcds_order(self, detail: dict) -> None:
        order = "HABCDS"
        for spread in detail["spreads"]:
            letters = [c for c in spread["notation"] if c in order]
            assert letters == sorted(letters, key=order.index), spread["notation"]

    def test_points_respect_champions_limits(self, detail: dict) -> None:
        """252 EV 표기는 이 프로젝트에 존재하지 않는다. 스탯당 32, 총합 66."""
        for spread in detail["spreads"]:
            points = spread["points"]
            assert all(0 <= value <= 32 for value in points.values()), points
            assert sum(points.values()) <= 66, points


class TestMissingData:
    """부정 테스트 — 없는 것을 물으면 404 다. 500 이 아니다."""

    def test_unknown_pokemon_is_404(self, client: TestClient) -> None:
        response = client.get("/api/v1/meta/pokemon/notapokemon")
        assert response.status_code == 404

    def test_unknown_format_is_rejected_by_schema(self, client: TestClient) -> None:
        response = client.get("/api/v1/meta/usage", params={"format": "Triples"})
        assert response.status_code == 422


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
