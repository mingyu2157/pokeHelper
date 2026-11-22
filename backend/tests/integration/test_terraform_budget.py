"""비용 알림이 실제로 누군가에게 간다 (ADR-020 세부사항 8).

★ 이 파일이 막는 것 ★

청구 알림은 **없어도 아무것도 안 깨진다.** 서비스는 멀쩡히 돌고, 화면도 정상
이고, 테스트도 전부 초록이다. 그래서 사람이 못 잡는다 — ADR-013(출처 표기)·
ADR-015(DB 권한)·ADR-019(선결 조건)와 같은 모양이다.

그런데 **없는 것보다 나쁜 상태가 있다**: 예산은 만들어졌는데 **수신자가
비어 있는 것**. 그러면 콘솔에 예산이 보이고 "알림을 달아뒀다"고 믿게 되는데,
실제로는 아무에게도 가지 않는다. 월말 청구서로 처음 안다.

그리고 **예측 알림이 빠지는 것**도 조용한 실패다. 실제 비용 알림만 있으면
"이미 쓴 돈"만 보게 되고, 월초에 켜진 NAT 는 한도에 닿을 때까지 조용하다.
Budgets 를 고른 이유 자체가 예측인데 그게 빠지면 CloudWatch 와 다를 게 없다.

범위와 한계는 `tests/terraform_support.py` 머리말 참조 — **`.tf` 소스를 읽고
실제 AWS 계정은 보지 않는다.**
"""

from __future__ import annotations

from typing import Any

import pytest

from tests.terraform_support import blocks, load_documents, resources

BUDGET = "aws_budgets_budget"


@pytest.fixture(scope="module")
def documents() -> list[dict[str, Any]]:
    return load_documents()


@pytest.fixture(scope="module")
def notifications(documents: list[dict[str, Any]]) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    for body in resources(documents, BUDGET).values():
        found.extend(blocks(body, "notification"))
    return found


class TestBudgetExists:
    """예산이 선언돼 있는가."""

    def test_a_monthly_cost_budget_is_declared(self, documents: list[dict[str, Any]]) -> None:
        budgets = resources(documents, BUDGET)
        assert budgets, "비용 예산이 없다. 과금 자원을 올리기 전에 둔다 (ADR-020 세부사항 8)"

        for name, body in budgets.items():
            assert body.get("budget_type") == "COST", f"{name}: COST 예산이어야 한다"
            assert body.get("time_unit") == "MONTHLY", f"{name}: 월 단위여야 한다"
            assert body.get("limit_unit") == "USD", f"{name}: 통화가 USD 여야 한다"

    def test_the_limit_comes_from_a_variable(self, documents: list[dict[str, Any]]) -> None:
        """금액을 코드에 박으면 바꾸려고 `.tf` 를 고치게 된다.

        ADR-020 의 비용표와 연결된 값이라 변수로 두고 설명을 붙여야 한다.
        """
        for name, body in resources(documents, BUDGET).items():
            amount = str(body.get("limit_amount", ""))
            assert "var." in amount, f"{name}: limit_amount 가 변수가 아니다 ({amount!r})"


class TestEveryNotificationReachesSomeone:
    """★ 수신자 없는 알림은 **없는 것보다 나쁘다.**

    콘솔에 예산이 보이니 "알림을 달아뒀다"고 믿게 되는데 아무에게도 안 간다.
    """

    def test_every_notification_has_a_subscriber(self, notifications: list[dict[str, Any]]) -> None:
        offenders = [
            n
            for n in notifications
            if not (n.get("subscriber_email_addresses") or n.get("subscriber_sns_topic_arns"))
        ]
        assert not offenders, f"수신자가 없는 알림이 있다: {offenders}"

    def test_subscribers_are_not_hardcoded(self, notifications: list[dict[str, Any]]) -> None:
        """주소를 `.tf` 에 박으면 저장소에 개인 이메일이 들어간다."""
        offenders = [
            n
            for n in notifications
            if "var." not in str(n.get("subscriber_email_addresses", ""))
            and n.get("subscriber_email_addresses")
        ]
        assert not offenders, f"이메일이 코드에 박혀 있다: {offenders}"


class TestForecastIsWatched:
    """★ 예측 알림이 Budgets 를 고른 이유다 (ADR-020 결과).

    실제 비용 알림만 있으면 **이미 쓴 돈**만 보게 된다. 월초에 실수로 켠
    NAT Gateway 는 한도에 닿을 때까지 조용하다. CloudWatch 청구 알람을 버리고
    Budgets 로 온 이유가 이것이라, 이게 빠지면 바꾼 의미가 사라진다.
    """

    def test_at_least_one_notification_is_forecasted(
        self, notifications: list[dict[str, Any]]
    ) -> None:
        kinds = {n.get("notification_type") for n in notifications}
        assert "FORECASTED" in kinds, (
            f"예측 알림이 없다 — 이미 쓴 돈만 보게 된다. 지금 있는 것: {sorted(kinds)}"
        )

    def test_actual_spend_is_also_watched(self, notifications: list[dict[str, Any]]) -> None:
        """예측만 있으면 반대쪽이 빈다. 예측은 틀릴 수 있다."""
        kinds = {n.get("notification_type") for n in notifications}
        assert "ACTUAL" in kinds, f"실제 비용 알림이 없다: {sorted(kinds)}"

    def test_warning_comes_before_the_limit(self, notifications: list[dict[str, Any]]) -> None:
        """★ 한도에 닿아서야 울리는 알림은 늦다.

        실제 비용 알림 중 **100% 미만** 이 하나는 있어야 한다. 전부 100% 면
        "한도를 넘었다"만 알려주고 그 전에는 조용하다 (ADR-016 에서 경보를
        둘로 나눈 것과 같은 이유).
        """
        early = [
            n
            for n in notifications
            if n.get("notification_type") == "ACTUAL" and float(n.get("threshold", 100)) < 100
        ]
        assert early, "한도 도달 전에 울리는 실제 비용 알림이 없다"


class TestTheCheckIsNotVacuous:
    """전제 선행 단정 — 검사 대상이 실제로 **있었다**.

    파싱이 어긋나 빈 리스트가 나오면 위 단정들이 공짜로 통과한다
    (TEST_STRATEGY 3-9). 같은 함정을 두 번 겪었다 — `.tf` 파서의 따옴표
    문제와, 401 응답을 "지표가 없다"로 읽은 교차검증.
    """

    def test_notifications_were_parsed(self, notifications: list[dict[str, Any]]) -> None:
        assert len(notifications) >= 2, f"알림이 너무 적다 — 파싱 문제일 수 있다: {notifications}"
        assert all("threshold" in n for n in notifications), (
            f"threshold 가 없는 알림이 있다 — 블록 파싱이 어긋났다: {notifications}"
        )
