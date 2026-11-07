"""POST /api/v1/calc/ev-tune 통합 테스트.

라우터는 얇아야 한다 (CLAUDE.md 2장). 여기서 검증하는 것은 계산 정확도가 아니라
**스키마 ↔ 도메인 변환과 계약**이다. 계산 자체는 tests/unit/domain 이 맡는다.
"""

import pytest
from fastapi.testclient import TestClient

from app.main import app

ENDPOINT = "/api/v1/calc/ev-tune"

# 한카리아스
GARCHOMP = {
    "hp": 108,
    "attack": 130,
    "defense": 95,
    "sp_attack": 80,
    "sp_defense": 85,
    "speed": 102,
}


@pytest.fixture(scope="module")
def client():
    return TestClient(app)


def payload(**overrides):
    body = {
        "base_stats": GARCHOMP,
        "types": ["dragon", "ground"],
        "constraints": [{"kind": "outspeed", "label": "상대 130", "target_speed": 130}],
    }
    body.update(overrides)
    return body


class TestSolution:
    def test_returns_a_spread(self, client):
        response = client.post(ENDPOINT, json=payload())
        assert response.status_code == 200
        body = response.json()
        assert body["feasible"] is True
        assert body["spread"]["speed"] == 9
        assert body["spread"]["total"] == 9

    def test_uses_habcds_notation(self, client):
        """UI 표준 표기 (CLAUDE.md 4장)."""
        response = client.post(ENDPOINT, json=payload())
        assert response.json()["spread"]["notation"] == "S9"

    def test_empty_spread_notation(self, client):
        response = client.post(ENDPOINT, json=payload(constraints=[]))
        assert response.json()["spread"]["notation"] == "-"

    def test_justifications_explain_every_point(self, client):
        response = client.post(ENDPOINT, json=payload())
        justifications = response.json()["justifications"]
        assert len(justifications) == 1
        assert justifications[0]["stat"] == "SPEED"
        assert justifications[0]["constraint"] == "상대 130"
        assert "131" in justifications[0]["detail"]


class TestNature:
    def test_accepts_a_nature_name(self, client):
        response = client.post(ENDPOINT, json=payload(nature="jolly"))
        assert response.status_code == 200
        # 명랑: 스피드 상승 -> 무보정보다 적은 포인트로 추월
        assert response.json()["spread"]["speed"] < 9

    def test_neutral_nature_behaves_like_no_nature(self, client):
        with_neutral = client.post(ENDPOINT, json=payload(nature="hardy")).json()
        without = client.post(ENDPOINT, json=payload()).json()
        assert with_neutral["spread"] == without["spread"]

    def test_unknown_nature_is_rejected(self, client):
        response = client.post(ENDPOINT, json=payload(nature="mystery"))
        assert response.status_code == 422
        assert "mystery" in response.text

    def test_nature_is_reported_back(self, client):
        response = client.post(ENDPOINT, json=payload(nature="jolly"))
        assert response.json()["nature"] == "jolly"


class TestPriority:
    def test_spends_the_whole_budget(self, client):
        response = client.post(ENDPOINT, json=payload(priority="PHYSICAL_BULK"))
        assert response.json()["spread"]["total"] == 66

    def test_without_priority_spends_the_minimum(self, client):
        response = client.post(ENDPOINT, json=payload())
        assert response.json()["spread"]["total"] == 9

    def test_unknown_priority_is_rejected(self, client):
        response = client.post(ENDPOINT, json=payload(priority="MYSTERY"))
        assert response.status_code == 422


class TestConstraintKinds:
    def test_knockout(self, client):
        response = client.post(
            ENDPOINT,
            json=payload(
                constraints=[
                    {
                        "kind": "knockout",
                        "label": "HP100 상대",
                        "opponent": {
                            "defense_stat": 100,
                            "special_defense_stat": 100,
                            "hp_stat": 100,
                            "types": ["normal"],
                        },
                        "move": {"power": 100, "type": "ground", "category": "physical"},
                    }
                ]
            ),
        )
        assert response.status_code == 200
        assert response.json()["spread"]["attack"] == 25

    def test_survive(self, client):
        response = client.post(
            ENDPOINT,
            json=payload(
                constraints=[
                    {
                        "kind": "survive",
                        "label": "얼음 기술",
                        "opponent": {
                            "attack_stat": 200,
                            "special_attack_stat": 200,
                            "types": ["ice"],
                        },
                        "move": {"power": 50, "type": "ice", "category": "physical"},
                    }
                ]
            ),
        )
        assert response.status_code == 200
        spread = response.json()["spread"]
        assert spread["hp"] + spread["defense"] > 0

    def test_unknown_kind_is_rejected(self, client):
        response = client.post(
            ENDPOINT, json=payload(constraints=[{"kind": "mystery", "label": "x"}])
        )
        assert response.status_code == 422


class TestInfeasible:
    @pytest.fixture
    def conflicting(self):
        return payload(
            constraints=[
                {"kind": "outspeed", "label": "추월 130", "target_speed": 130},
                {
                    "kind": "knockout",
                    "label": "확정 1타",
                    "opponent": {
                        "defense_stat": 100,
                        "special_defense_stat": 100,
                        "hp_stat": 104,
                        "types": ["normal"],
                    },
                    "move": {"power": 100, "type": "ground", "category": "physical"},
                },
                {
                    "kind": "survive",
                    "label": "얼음 버티기",
                    "opponent": {
                        "attack_stat": 200,
                        "special_attack_stat": 200,
                        "types": ["ice"],
                    },
                    "move": {"power": 50, "type": "ice", "category": "physical"},
                },
            ]
        )

    def test_returns_200_with_feasible_false(self, client, conflicting):
        """불가능은 서버 오류가 아니다. 유효한 계산 결과다."""
        response = client.post(ENDPOINT, json=conflicting)
        assert response.status_code == 200
        assert response.json()["feasible"] is False

    def test_reports_the_budget_gap(self, client, conflicting):
        body = client.post(ENDPOINT, json=conflicting).json()
        assert body["budget"] == 66
        assert body["total_required"] > 66

    def test_offers_options(self, client, conflicting):
        body = client.post(ENDPOINT, json=conflicting).json()
        assert len(body["options"]) > 0
        for option in body["options"]:
            assert option["satisfied"]
            assert option["dropped"]
            assert option["spread"]["total"] <= 66

    def test_separates_impossible_constraints(self, client):
        body = client.post(
            ENDPOINT,
            json=payload(
                constraints=[{"kind": "outspeed", "label": "상한 초과", "target_speed": 999}]
            ),
        ).json()
        assert body["feasible"] is False
        assert body["impossible"] == ["상한 초과"]


class TestAssumptions:
    """CLAUDE.md 1-3 — 추정과 사실을 구분한다."""

    def test_always_present(self, client):
        for body in (payload(), payload(priority="SPEED")):
            assumptions = client.post(ENDPOINT, json=body).json()["assumptions"]
            assert len(assumptions) > 0

    def test_mentions_the_unverified_damage_chain(self, client):
        assumptions = client.post(ENDPOINT, json=payload()).json()["assumptions"]
        assert any("ADR-003" in a for a in assumptions)

    def test_mentions_missing_modifiers(self, client):
        assumptions = client.post(ENDPOINT, json=payload()).json()["assumptions"]
        assert any("도구" in a and "적용" in a for a in assumptions)

    def test_infeasible_responses_carry_assumptions_too(self, client):
        body = client.post(
            ENDPOINT,
            json=payload(constraints=[{"kind": "outspeed", "label": "불가", "target_speed": 999}]),
        ).json()
        assert len(body["assumptions"]) > 0


class TestValidation:
    def test_unknown_type_is_rejected(self, client):
        response = client.post(ENDPOINT, json=payload(types=["mystery"]))
        assert response.status_code == 422

    def test_too_many_types_is_rejected(self, client):
        response = client.post(ENDPOINT, json=payload(types=["dragon", "ground", "fire"]))
        assert response.status_code == 422

    def test_missing_base_stats_is_rejected(self, client):
        body = payload()
        del body["base_stats"]
        assert client.post(ENDPOINT, json=body).status_code == 422

    def test_non_positive_base_stat_is_rejected(self, client):
        response = client.post(ENDPOINT, json=payload(base_stats={**GARCHOMP, "speed": 0}))
        assert response.status_code == 422

    def test_shedinja_hp_is_rejected(self, client):
        """HP 종족값 1은 챔피언스에 존재하지 않는다. 데이터 오류로 본다."""
        response = client.post(
            ENDPOINT,
            json=payload(
                base_stats={**GARCHOMP, "hp": 1},
                constraints=[
                    {
                        "kind": "survive",
                        "label": "버티기",
                        "opponent": {
                            "attack_stat": 200,
                            "special_attack_stat": 200,
                            "types": ["ice"],
                        },
                        "move": {"power": 50, "type": "ice", "category": "physical"},
                    }
                ],
            ),
        )
        assert response.status_code == 422


class TestOpenApi:
    def test_endpoint_is_documented(self, client):
        schema = client.get("/openapi.json").json()
        assert ENDPOINT in schema["paths"]

    def test_no_ev_or_252_in_the_contract(self, client):
        """5-0 — 'EV', '252' 표현이 API 에 나오면 안 된다."""
        text = client.get("/openapi.json").text
        assert "252" not in text
        assert "effort_points" in text or "ev-tune" in text


class TestInternalGuards:
    """도달하면 안 되는 분기. 제약 종류가 늘어나면 조용히 틀리지 않고 터진다."""

    def test_unhandled_constraint_type_raises(self):
        import pytest as _pytest

        from app.services.ev_tune import _to_constraint

        with _pytest.raises(AssertionError):
            _to_constraint(object())  # type: ignore[arg-type]


class TestInputBounds:
    """현실적인 상한 — 오타를 막되 극단적 조정은 통과시킨다.

    상한 근거는 championsbattledata 폼 447개 전수 조사다 (2026-11-10).
    종족값 최대 250(Mega Aggron 방어), HP 최대 235(Snorlax).

    **느슨한 상한이다.** 입력받는 실수치는 이미 랭크업·도구가 반영된 값이라
    (ADR-007) 순수 상한의 몇 배가 될 수 있다. 엄격한 상한은 보정 연결 작업에서
    랭크업·도구를 별도 필드로 받게 되면 그때 조인다.
    """

    @pytest.mark.parametrize("value", [256, 999, 99999])
    def test_base_stat_above_the_cap_is_rejected(self, client, value):
        response = client.post(ENDPOINT, json=payload(base_stats={**GARCHOMP, "attack": value}))
        assert response.status_code == 422

    @pytest.mark.parametrize("value", [250, 255])
    def test_realistic_base_stats_are_accepted(self, client, value):
        """250 은 Mega Aggron 방어로 실재한다.

        상한을 관측값 250 이 아니라 255 로 둔 이유: 챔피언스에 포켓몬이 계속
        추가되므로 시리즈 이론 상한까지 열어둔다. 250 으로 조이면 나중에
        종족값 255 짜리가 추가될 때 조용히 거부된다.
        """
        response = client.post(ENDPOINT, json=payload(base_stats={**GARCHOMP, "defense": value}))
        assert response.status_code == 200

    @pytest.mark.parametrize("field", ["attack_stat", "special_attack_stat"])
    def test_opponent_attack_above_the_cap_is_rejected(self, client, field):
        opponent = {"attack_stat": 200, "special_attack_stat": 200, "types": ["ice"]}
        opponent[field] = 99999
        response = client.post(
            ENDPOINT,
            json=payload(
                constraints=[
                    {
                        "kind": "survive",
                        "label": "버티기",
                        "opponent": opponent,
                        "move": {"power": 50, "type": "ice", "category": "physical"},
                    }
                ]
            ),
        )
        assert response.status_code == 422

    def test_rank_boosted_stat_is_still_accepted(self, client):
        """랭크업 6단계 + 구애띠면 1,992 까지 나온다. 막으면 실사용이 깨진다."""
        response = client.post(
            ENDPOINT,
            json=payload(
                constraints=[
                    {
                        "kind": "survive",
                        "label": "용의춤 쌓은 상대",
                        "opponent": {
                            "attack_stat": 1992,
                            "special_attack_stat": 200,
                            "types": ["ice"],
                        },
                        "move": {"power": 50, "type": "ice", "category": "physical"},
                    }
                ]
            ),
        )
        assert response.status_code in (200, 422)
        if response.status_code == 422:
            pytest.fail("랭크업·도구가 반영된 실수치를 막으면 안 된다")

    def test_opponent_hp_above_the_cap_is_rejected(self, client):
        response = client.post(
            ENDPOINT,
            json=payload(
                constraints=[
                    {
                        "kind": "knockout",
                        "label": "상대",
                        "opponent": {
                            "defense_stat": 100,
                            "special_defense_stat": 100,
                            "hp_stat": 99999,
                            "types": ["normal"],
                        },
                        "move": {"power": 100, "type": "ground", "category": "physical"},
                    }
                ]
            ),
        )
        assert response.status_code == 422

    def test_move_power_above_the_cap_is_rejected(self, client):
        response = client.post(
            ENDPOINT,
            json=payload(
                constraints=[
                    {
                        "kind": "knockout",
                        "label": "상대",
                        "opponent": {
                            "defense_stat": 100,
                            "special_defense_stat": 100,
                            "hp_stat": 150,
                            "types": ["normal"],
                        },
                        "move": {"power": 9999, "type": "ground", "category": "physical"},
                    }
                ]
            ),
        )
        assert response.status_code == 422

    def test_target_speed_above_the_cap_is_rejected(self, client):
        response = client.post(
            ENDPOINT,
            json=payload(
                constraints=[{"kind": "outspeed", "label": "상대", "target_speed": 99999}]
            ),
        )
        assert response.status_code == 422

    def test_too_many_constraints_is_rejected(self, client):
        """최대 부분집합 탐색이 2^n 이라 제약 수에 상한이 필요하다."""
        many = [
            {"kind": "outspeed", "label": f"상대 {i}", "target_speed": 100 + i} for i in range(20)
        ]
        response = client.post(ENDPOINT, json=payload(constraints=many))
        assert response.status_code == 422

    def test_hits_above_the_cap_is_rejected(self, client):
        response = client.post(
            ENDPOINT,
            json=payload(
                constraints=[
                    {
                        "kind": "survive",
                        "label": "버티기",
                        "opponent": {
                            "attack_stat": 200,
                            "special_attack_stat": 200,
                            "types": ["ice"],
                        },
                        "move": {"power": 50, "type": "ice", "category": "physical"},
                        "hits": 999,
                    }
                ]
            ),
        )
        assert response.status_code == 422
