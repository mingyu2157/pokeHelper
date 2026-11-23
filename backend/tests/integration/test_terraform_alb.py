"""ALB 가 **평문으로 서비스하거나 라우팅이 어긋나는 방향**을 막는다 (`infra/terraform/alb.tf`).

★ 이 파일이 막는 것 ★

ALB 설정의 나쁜 변경은 대부분 "사이트가 열린다"로 보인다. 다섯 가지가 특히 그렇다.

- **80 포트를 forward 로 바꾸기** — 사이트가 **평문으로** 열린다. 브라우저는
  경고를 띄우지만 동작하고, 쿠키와 토큰이 그대로 네트워크를 지나간다.
  리다이렉트와 forward 는 설정 한 줄 차이고 **둘 다 200 을 돌려준다.**
- **낡은 TLS 정책으로 바꾸기** — TLS 1.0/1.1 을 받아도 최신 브라우저는 1.3 으로
  붙으므로 **우리 눈에는 아무 차이가 없다.**
- **인증서를 `validation` 이 아니라 인증서 자원에서 가져오기** — 값은 같지만
  의존 관계가 사라져서, 첫 apply 가 `Pending validation` 인 인증서를 붙이려 하다
  깨진다. 두 번째 apply 는 성공하므로 **재현이 어렵다.**
- **헬스체크 matcher 를 넓히기** — `200-499` 로 두면 500 만 아니면 정상이 되어
  **깨진 앱이 계속 트래픽을 받는다.**
- **`/metrics` 를 라우팅에 넣기** — ADR-019 의 토큰이 **유일한 방어선**이 된다.
  지금은 라우팅 자체가 없어서 토큰이 두 번째 겹이다.

범위와 한계는 `tests/terraform_support.py` 머리말 참조 — **`.tf` 소스를 읽고
실제 AWS 계정은 보지 않는다.**
"""

from __future__ import annotations

from typing import Any

import pytest

from tests.terraform_support import load_documents, local_values, resources

LB = "aws_lb"
TARGET_GROUP = "aws_lb_target_group"
LISTENER = "aws_lb_listener"
LISTENER_RULE = "aws_lb_listener_rule"

# TLS 1.2 미만을 받지 않는 정책만 허용한다. 새 정책을 쓰려면 **그게 1.0/1.1 을
# 받지 않는지 확인한 뒤** 여기에 더한다. 목록을 늘리는 것이 곧 검토 기록이다.
ALLOWED_SSL_POLICIES = {
    "ELBSecurityPolicy-TLS13-1-2-2021-06",
    "ELBSecurityPolicy-TLS13-1-2-Res-2021-06",
    "ELBSecurityPolicy-TLS-1-2-2017-01",
    "ELBSecurityPolicy-TLS-1-2-Ext-2018-06",
}

EXPECTED_TARGETS = {"backend", "frontend", "grafana"}

FRONTEND_TG = '${aws_lb_target_group.app["frontend"].arn}'
BACKEND_TG = '${aws_lb_target_group.app["backend"].arn}'


def listed(value: Any) -> list[Any]:
    """hcl2 는 원소가 하나면 리스트를 벗겨서 준다. 항상 리스트로 되돌린다."""
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


@pytest.fixture(scope="module")
def documents() -> list[dict[str, Any]]:
    return load_documents()


@pytest.fixture(scope="module")
def alb(documents: list[dict[str, Any]]) -> dict[str, Any]:
    found = resources(documents, LB)
    assert set(found) == {"main"}, found
    return found["main"]


@pytest.fixture(scope="module")
def listeners(documents: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return resources(documents, LISTENER)


@pytest.fixture(scope="module")
def rules(documents: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return resources(documents, LISTENER_RULE)


@pytest.fixture(scope="module")
def target_groups(documents: list[dict[str, Any]]) -> dict[str, Any]:
    """`local.target_groups` — 포트와 헬스체크 경로의 실제 값이 여기 있다.

    리소스 쪽은 `for_each` 라서 `${each.value.port}` 라는 **문자열**로 파싱된다.
    값을 단정하려면 locals 를 봐야 한다.
    """
    locals_ = local_values(documents)
    assert "target_groups" in locals_, "alb.tf 에서 local.target_groups 를 찾지 못했다"
    return locals_["target_groups"]


class TestPlaintextIsNeverServed:
    """★ 이 클래스가 가장 중요하다.

    부정 테스트 — 80 포트가 콘텐츠를 **서비스할** 수 있는 모든 형태를 막는다.
    리다이렉트와 forward 는 사용자에게 둘 다 "사이트가 열린다"로 보인다.
    """

    def test_port_80_only_redirects(self, listeners: dict[str, dict[str, Any]]) -> None:
        action = listeners["http"]["default_action"]
        assert action["type"] == "redirect", (
            "80 포트가 리다이렉트가 아니다 — 사이트가 평문으로 열린다."
        )
        assert "target_group_arn" not in action

    def test_the_redirect_goes_to_https_permanently(
        self, listeners: dict[str, dict[str, Any]]
    ) -> None:
        redirect = listeners["http"]["default_action"]["redirect"]
        assert redirect["protocol"] == "HTTPS"
        assert redirect["port"] == "443"
        # 301(영구). 302 면 브라우저가 매번 http 로 먼저 간다.
        assert redirect["status_code"] == "HTTP_301"

    def test_no_rule_hangs_off_the_http_listener(self, rules: dict[str, dict[str, Any]]) -> None:
        """★ 규칙을 80 리스너에 붙이면 **그 경로만** 평문으로 뚫린다.

        기본 동작이 리다이렉트라도 규칙이 먼저 평가된다. 전체가 아니라 일부만
        평문이 되므로 더 안 보인다.
        """
        for name, rule in rules.items():
            assert rule["listener_arn"] == "${aws_lb_listener.https.arn}", name

    def test_the_https_listener_is_https(self, listeners: dict[str, dict[str, Any]]) -> None:
        """전제 선행 단정 — 위 검사들이 의미를 가지려면 443 쪽이 HTTPS 여야 한다."""
        assert listeners["https"]["protocol"] == "HTTPS"
        assert listeners["https"]["port"] == 443


class TestTlsPolicyStaysModern:
    """허용 목록 방식 — 새 정책을 쓰려면 검토해서 목록에 더해야 한다.

    ★ 낡은 정책으로 바꿔도 **우리 브라우저는 1.3 으로 붙어서 차이가 안 보인다.**
    """

    def test_ssl_policy_is_allowlisted(self, listeners: dict[str, dict[str, Any]]) -> None:
        policy = listeners["https"]["ssl_policy"]
        assert policy in ALLOWED_SSL_POLICIES, (
            f"검토되지 않은 TLS 정책이다: {policy}. "
            "1.0/1.1 을 받지 않는지 확인한 뒤 ALLOWED_SSL_POLICIES 에 더한다."
        )


class TestCertificateOrderingIsEnforced:
    """차등 단정 — 값이 같아도 **의존 관계가 다른** 두 참조를 구분한다."""

    def test_listener_waits_for_issuance(self, listeners: dict[str, dict[str, Any]]) -> None:
        """★ `aws_acm_certificate.main.arn` 을 쓰면 첫 apply 가 깨진다.

        값은 같다. 다른 것은 Terraform 이 보는 선행 조건이다. 두 번째 apply 는
        성공하므로 "한 번 더 돌리면 되는" 문제로 넘어가기 쉽다.
        """
        assert (
            listeners["https"]["certificate_arn"]
            == "${aws_acm_certificate_validation.main.certificate_arn}"
        )


class TestRoutingMatchesTheTopology:
    """ADR-020 의 경로 기반 라우팅을 고정한다 — `/api/*` → 백엔드, `/*` → 프론트."""

    def test_unmatched_traffic_goes_to_the_frontend(
        self, listeners: dict[str, dict[str, Any]]
    ) -> None:
        action = listeners["https"]["default_action"]
        assert action["type"] == "forward"
        assert action["target_group_arn"] == FRONTEND_TG

    def test_api_and_health_go_to_the_backend(self, rules: dict[str, dict[str, Any]]) -> None:
        """★ `/health` 가 여기 있어야 README 4장의 수락 기준을 외부에서 확인할 수 있다.

        FastAPI 의 헬스 라우터는 접두사가 없어서 경로가 `/health` 다. `/api/*`
        뿐이면 프론트로 가서 404 가 된다.
        """
        rule = rules["backend"]
        patterns = set(listed(rule["condition"]["path_pattern"]["values"]))
        assert "/api/*" in patterns
        assert "/health" in patterns
        assert rule["action"]["target_group_arn"] == BACKEND_TG

    def test_grafana_matches_with_and_without_the_trailing_slash(
        self, rules: dict[str, dict[str, Any]]
    ) -> None:
        """경계값 분석 — ALB 의 `/grafana/*` 는 `/grafana` 에 매칭되지 않는다.

        하나만 적으면 "슬래시를 붙이면 되는데 안 붙이면 404" 가 된다.
        """
        patterns = set(listed(rules["grafana"]["condition"]["path_pattern"]["values"]))
        assert {"/grafana", "/grafana/*"} <= patterns

    def test_metrics_is_not_routed_from_the_internet(
        self, rules: dict[str, dict[str, Any]]
    ) -> None:
        """★ ADR-019 의 `METRICS_TOKEN` 이 유일한 방어선이 되지 않게 한다.

        Prometheus 사이드카는 **같은 태스크 안에서 localhost** 로 긁으므로 ALB
        라우팅이 필요 없다. 규칙을 더하는 순간 토큰 하나만 남는다.
        """
        for name, rule in rules.items():
            for pattern in listed(rule["condition"]["path_pattern"]["values"]):
                assert not pattern.startswith("/metrics"), (
                    f"{name} 규칙이 /metrics 를 라우팅한다 — 토큰이 유일한 방어선이 된다."
                )

    def test_rule_priorities_are_unique(self, rules: dict[str, dict[str, Any]]) -> None:
        """같은 우선순위는 AWS 가 거부한다. 중복을 apply 전에 잡는다."""
        priorities = [rule["priority"] for rule in rules.values()]
        assert len(priorities) == len(set(priorities)), priorities


class TestEveryTargetGroupIsStrict:
    """전수 조합 — 타깃 그룹 **셋 모두**에 같은 규칙을 적용한다.

    하나만 느슨해도 그 경로의 고장이 보이지 않는다.
    """

    def test_all_three_exist(self, target_groups: dict[str, Any]) -> None:
        assert set(target_groups) == EXPECTED_TARGETS

    def test_target_type_is_ip_for_fargate(self, documents: list[dict[str, Any]]) -> None:
        """`instance` 는 EC2 기반 ECS 용이다. Fargate 는 등록 자체가 안 된다."""
        assert resources(documents, TARGET_GROUP)["app"]["target_type"] == "ip"

    def test_only_200_counts_as_healthy(self, documents: list[dict[str, Any]]) -> None:
        """★ 넓히면 "500 을 돌려주면서 정상"이 가능해진다."""
        health_check = resources(documents, TARGET_GROUP)["app"]["health_check"]
        assert health_check["matcher"] == "200"
        assert health_check["enabled"] is True

    def test_failure_is_noticed_within_a_minute(self, documents: list[dict[str, Any]]) -> None:
        """경계값 분석 — `interval × unhealthy_threshold` 가 고장 감지 시간이다."""
        health_check = resources(documents, TARGET_GROUP)["app"]["health_check"]
        assert health_check["interval"] * health_check["unhealthy_threshold"] <= 60

    def test_deploys_are_not_blocked_by_the_default_drain(
        self, documents: list[dict[str, Any]]
    ) -> None:
        """기본값 300초면 배포 한 번에 5분이 그냥 지나간다."""
        assert resources(documents, TARGET_GROUP)["app"]["deregistration_delay"] <= 60

    @pytest.mark.parametrize("key", sorted(EXPECTED_TARGETS))
    def test_each_target_declares_a_health_check_path(
        self, target_groups: dict[str, Any], key: str
    ) -> None:
        path = target_groups[key]["health_check_path"]
        assert isinstance(path, str) and path.startswith("/"), (key, path)


class TestTheAlbIsTheOnlyDoorAndItIsReachable:
    """ALB 의 노출 형태를 고정한다."""

    def test_it_faces_the_internet(self, alb: dict[str, Any]) -> None:
        assert alb["internal"] is False

    def test_it_sits_in_the_public_subnets(self, alb: dict[str, Any]) -> None:
        """프라이빗 서브넷에 두면 인터넷에서 닿지 않는다."""
        assert alb["subnets"] == "${[for subnet in aws_subnet.public : subnet.id]}"

    def test_it_carries_only_the_alb_security_group(self, alb: dict[str, Any]) -> None:
        """★ 태스크 보안그룹을 같이 붙이면 `0.0.0.0/0` 개방이 한 겹 늘어난다.

        `test_terraform_security_groups.py` 가 "ALB 보안그룹만 전체 개방"을
        단정하는데, 그 전제가 ALB 가 **그 그룹만** 쓴다는 것이다.
        """
        assert listed(alb["security_groups"]) == ["${aws_security_group.alb.id}"]

    def test_forged_headers_are_dropped(self, alb: dict[str, Any]) -> None:
        """기본값이 아니라서 명시했다. 끄면 뒤의 프레임워크 해석에 보안이 걸린다."""
        assert alb["drop_invalid_header_fields"] is True

    def test_destroy_and_apply_can_round_trip(self, alb: dict[str, Any]) -> None:
        """★ RDS 와 **다른** 판단이다.

        ALB 는 상태가 없어서 몇 분이면 같은 구성으로 다시 만들 수 있고, 바뀌는
        DNS 이름은 alias 레코드가 따라간다. 모든 자원에 보호를 걸면 보호가
        의식에서 지워진다.
        """
        assert alb["enable_deletion_protection"] is False


class TestTheCheckIsNotVacuous:
    """전제 선행 단정 — 파싱이 빈 결과를 돌려주면 "없음" 단정이 전부 통과한다."""

    def test_both_listeners_were_parsed(self, listeners: dict[str, dict[str, Any]]) -> None:
        assert set(listeners) == {"https", "http"}, set(listeners)

    def test_both_rules_were_parsed(self, rules: dict[str, dict[str, Any]]) -> None:
        assert set(rules) == {"backend", "grafana"}, set(rules)

    def test_the_target_group_map_is_not_empty(self, target_groups: dict[str, Any]) -> None:
        assert len(target_groups) == len(EXPECTED_TARGETS) == 3
