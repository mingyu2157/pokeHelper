"""보안그룹이 실제로 좁은가 (ADR-020 결정 1, 세부사항 2).

★ 이 테스트가 막는 것 ★

ADR-020 은 비용을 이유로 ECS 태스크를 **퍼블릭 서브넷**에 두고 공인 IP 를
붙이기로 했다. 그래서 프라이빗 서브넷의 "라우팅이 없어서 못 닿는다"가 성립하지
않고, **보안그룹 한 줄이 유일한 방어선**이다.

그 한 줄이 틀려도 **서비스는 멀쩡히 응답한다.** 화면도 정상이다. 즉 사람은
못 잡는다. ADR-013(출처 표기)·ADR-015(DB 권한)·ADR-019(선결 조건)와 같은 모양
이다 — **열려 있어도 아무것도 깨지지 않는 종류의 결함**이다.

범위와 한계
-----------
이 테스트는 **`.tf` 소스를 읽는다. 실제 AWS 계정을 보지 않는다.**
`terraform plan` 은 자격증명이 필요해 CI 에서 돌릴 수 없기 때문이다. 따라서
**콘솔에서 손으로 고친 규칙은 다음 `apply` 전까지 잡히지 않는다.** 이 한계는
ADR-020 의 `## 포기한 것` 에 적혀 있다.

인바운드만 본다. 아웃바운드 `0.0.0.0/0` 은 외부에서 들어올 길을 늘리지 않고,
태스크는 ECR·SSM·CloudWatch·외부 API·DNS 로 나가야 한다. 아웃바운드를 좁히는
것은 실제 트래픽을 VPC 플로우 로그로 측정한 뒤에 할 일이다.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import hcl2
import pytest

# 컨테이너에서는 /opt/terraform 에 마운트되고(compose), CI 는 저장소에서 직접
# 돈다. `/app` 아래에 마운트하지 않는다 — 바인드 마운트는 기존 경로를 가린다
# (JOURNAL 2026-11-17).
_CANDIDATES = (
    Path("/opt/terraform"),
    Path(__file__).resolve().parents[3] / "infra" / "terraform",
)

# ★ 인터넷에서 직접 받아도 되는 보안그룹. **여기 적힌 것만** 허용한다.
#
# 이름을 테스트에 고정해둔 것이 핵심이다. 새로 하나를 열려면 이 집합을 고쳐야
# 하고, 그 수정이 리뷰에 보인다. 태그나 이름 규칙으로 판정하면 규칙을 흉내내는
# 것만으로 조용히 열 수 있다.
INTERNET_FACING = frozenset({"alb"})

ANY_IPV4 = "0.0.0.0/0"
ANY_IPV6 = "::/0"

SG_REFERENCE = re.compile(r"aws_security_group\.([A-Za-z0-9_-]+)\.id")


def terraform_dir() -> Path:
    for candidate in _CANDIDATES:
        if candidate.is_dir():
            return candidate
    # 건너뛰지 않는다. 디렉터리를 못 찾으면 검사가 통째로 사라지는데, 그건
    # "통과"가 아니라 "검증하지 않음"이다 (test_alert_rules.py 와 같은 원칙).
    pytest.fail(f"Terraform 디렉터리를 찾을 수 없다. 찾은 곳: {[str(c) for c in _CANDIDATES]}")


def unwrap(value: Any) -> Any:
    """python-hcl2 가 블록을 리스트로 감싸는 것을 벗긴다."""
    if isinstance(value, list) and len(value) == 1:
        return value[0]
    return value


def unquote(value: Any) -> Any:
    """★ python-hcl2 7.x 는 블록 라벨과 문자열 값에 따옴표를 **남긴다**.

    `aws_subnet` 이 아니라 `"aws_subnet"` 으로, `0.0.0.0/0` 이 아니라
    `"0.0.0.0/0"` 으로 나온다. 버전에 따라 다르므로, 따옴표가 없어도 그대로
    통과하도록(멱등) 만든다. 여기서 정규화하지 않으면 **모든 비교가 조용히
    빗나가고** 검사가 통째로 공회전한다 — 실제로 그렇게 됐고,
    `test_the_check_is_not_vacuous` 가 잡았다.
    """
    if isinstance(value, str) and len(value) >= 2 and value[0] == '"' and value[-1] == '"':
        return value[1:-1]
    return value


def clean(body: Any) -> Any:
    """파서가 끼워 넣은 메타 키(`__is_block__`·`__comments__`)를 걷어내고
    문자열 값의 따옴표를 벗긴다."""
    body = unwrap(body)
    if isinstance(body, dict):
        return {k: clean(v) for k, v in body.items() if not k.startswith("__")}
    if isinstance(body, list):
        return [clean(v) for v in body]
    return unquote(body)


@pytest.fixture(scope="module")
def documents() -> list[dict[str, Any]]:
    """`bootstrap/` 을 뺀 모든 `.tf` 를 파싱한다.

    bootstrap 은 state 저장소(S3·DynamoDB)만 만들고 VPC 를 건드리지 않는다.
    """
    root = terraform_dir()
    files = sorted(p for p in root.glob("*.tf"))
    assert files, f"{root} 에 .tf 파일이 없다"

    parsed = []
    for path in files:
        with path.open(encoding="utf-8") as handle:
            parsed.append(hcl2.load(handle))
    return parsed


def resources(documents: list[dict[str, Any]], kind: str) -> dict[str, dict[str, Any]]:
    """`kind` 타입 리소스를 {이름: 속성} 으로 모은다."""
    found: dict[str, dict[str, Any]] = {}
    for document in documents:
        for block in document.get("resource", []):
            for resource_type, bodies in block.items():
                if unquote(resource_type) != kind:
                    continue
                for name, body in bodies.items():
                    found[unquote(name)] = clean(body)
    return found


def target_group_name(rule: dict[str, Any]) -> str | None:
    """규칙이 붙은 보안그룹의 Terraform 이름."""
    match = SG_REFERENCE.search(str(rule.get("security_group_id", "")))
    return match.group(1) if match else None


def is_open_to_the_world(rule: dict[str, Any]) -> bool:
    return rule.get("cidr_ipv4") == ANY_IPV4 or rule.get("cidr_ipv6") == ANY_IPV6


class TestOnlyTheAlbFacesTheInternet:
    """★ 동등 분할 — 인바운드 규칙을 '전체 개방'과 '그 외'로 나눠 각각 단정한다."""

    def test_open_ingress_is_only_on_the_alb(self, documents: list[dict[str, Any]]) -> None:
        offenders: dict[str, str | None] = {}
        for name, rule in resources(documents, "aws_vpc_security_group_ingress_rule").items():
            if not is_open_to_the_world(rule):
                continue
            group = target_group_name(rule)
            if group not in INTERNET_FACING:
                offenders[name] = group

        assert not offenders, (
            f"ALB 가 아닌 보안그룹이 인터넷 전체에 열려 있다: {offenders}. "
            f"태스크는 퍼블릭 서브넷에 있어서 이게 곧 노출이다"
        )

    def test_everything_else_is_sourced_from_a_security_group(
        self, documents: list[dict[str, Any]]
    ) -> None:
        """★ CIDR 로 여는 것 자체를 막는다.

        `0.0.0.0/0` 만 검사하면 `10.0.0.0/8` 이나 '내 집 IP' 가 통과한다.
        전자는 VPC 전체에 여는 것이고, 후자는 IP 가 바뀌면 조용히 쓸모없어진
        규칙으로 남는다. ALB 가 아닌 보안그룹은 **반드시 다른 보안그룹을
        소스로** 쓴다.
        """
        offenders: dict[str, Any] = {}
        for name, rule in resources(documents, "aws_vpc_security_group_ingress_rule").items():
            group = target_group_name(rule)
            if group in INTERNET_FACING:
                continue
            if "referenced_security_group_id" not in rule:
                offenders[name] = {k: v for k, v in rule.items() if k.startswith("cidr")}

        assert not offenders, f"보안그룹 참조가 아닌 소스를 쓴다: {offenders}"

    def test_the_check_is_not_vacuous(self, documents: list[dict[str, Any]]) -> None:
        """전제 선행 단정 — 검사 대상이 실제로 **있었다**.

        파싱이 실패해 빈 딕셔너리가 나오면 위 두 테스트가 공짜로 통과한다
        (TEST_STRATEGY 3-9). `test_alert_rules.py` 가 401 응답을 '지표가 없다'로
        읽었던 것과 같은 함정이다.
        """
        rules = resources(documents, "aws_vpc_security_group_ingress_rule")
        groups = resources(documents, "aws_security_group")

        assert len(groups) >= 4, f"보안그룹이 너무 적다: {sorted(groups)}"
        assert len(rules) >= 6, f"인바운드 규칙이 너무 적다: {sorted(rules)}"
        assert any(is_open_to_the_world(r) for r in rules.values()), (
            "전체 개방 규칙이 하나도 없다. ALB 는 443 을 열어야 하므로 "
            "파싱이 잘못됐을 가능성이 높다"
        )
        assert any("referenced_security_group_id" in r for r in rules.values()), (
            "보안그룹 참조 규칙이 하나도 없다"
        )


class TestNoHiddenRules:
    """★ 검사를 우회하는 경로가 생기지 않는가."""

    def test_security_groups_declare_no_inline_rules(self, documents: list[dict[str, Any]]) -> None:
        """인라인 `ingress {}` 블록은 위 검사에 **보이지 않는다**.

        그리고 한 보안그룹에 인라인과 별도 리소스를 섞으면 Terraform 이 서로의
        규칙을 지운다. 둘 다 이유가 된다.
        """
        offenders = [
            name
            for name, body in resources(documents, "aws_security_group").items()
            if "ingress" in body or "egress" in body
        ]
        assert not offenders, (
            f"인라인 규칙 블록을 쓴 보안그룹: {offenders}. "
            f"aws_vpc_security_group_ingress_rule 로 분리한다"
        )

    def test_the_task_group_does_not_expose_prometheus(
        self, documents: list[dict[str, Any]]
    ) -> None:
        """Prometheus(9090)는 사이드카라 태스크 밖에서 닿을 이유가 없다.

        같은 태스크의 컨테이너끼리는 loopback 으로 통신하고 보안그룹을 타지
        않는다. 9090 규칙이 생겼다면 누군가 구조를 오해한 것이다.
        """
        exposed = [
            name
            for name, rule in resources(documents, "aws_vpc_security_group_ingress_rule").items()
            if rule.get("from_port") == 9090 or rule.get("to_port") == 9090
        ]
        assert not exposed, f"Prometheus 포트가 열려 있다: {exposed}"


class TestPrivateSubnetsHaveNoWayOut:
    """★ NAT 가 없다는 것이 설계의 일부다 (ADR-020 결정 1).

    프라이빗 라우트 테이블에 `0.0.0.0/0` 이 생기면 NAT Gateway 나 인스턴스가
    붙었다는 뜻이고, **월 $43 이 조용히 추가된다.** 비용은 테스트가 보지 않으면
    청구서로만 드러난다.
    """

    def test_the_private_route_table_has_no_default_route(
        self, documents: list[dict[str, Any]]
    ) -> None:
        private = resources(documents, "aws_route_table").get("private")
        assert private is not None, "프라이빗 라우트 테이블이 없다"

        routes = private.get("route", [])
        routes = routes if isinstance(routes, list) else [routes]
        defaults = [r for r in routes if unwrap(r).get("cidr_block") == ANY_IPV4]

        assert not defaults, (
            f"프라이빗 서브넷에 기본 경로가 생겼다: {defaults}. "
            f"NAT 를 붙였다면 ADR-020 결정 1 을 다시 열어야 한다 (월 $43)"
        )

    def test_there_is_no_nat_gateway(self, documents: list[dict[str, Any]]) -> None:
        nats = resources(documents, "aws_nat_gateway")
        assert not nats, f"NAT Gateway 가 선언됐다: {sorted(nats)}"


class TestSubnetsDoNotHandOutPublicIpsByDefault:
    """공인 IP 는 **태스크 단위로** 켠다 (ECS 서비스의 assign_public_ip).

    서브넷 기본값으로 켜두면 나중에 여기 무언가를 띄울 때 의도치 않게 공인 IP 가
    붙는다. 기본값은 끄고 필요한 곳에서만 켜는 쪽이 사고를 줄인다.
    """

    def test_public_subnets_do_not_auto_assign(self, documents: list[dict[str, Any]]) -> None:
        public = resources(documents, "aws_subnet").get("public")
        assert public is not None, "퍼블릭 서브넷 선언이 없다"
        assert public.get("map_public_ip_on_launch") is False, (
            "서브넷 기본값으로 공인 IP 를 주면 안 된다. ECS 서비스에서 켠다"
        )
