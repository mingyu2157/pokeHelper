"""DNS·TLS 설정이 **조용히 깨지는 방향**을 막는다 (`infra/terraform/dns.tf`).

★ 이 파일이 막는 것 ★

DNS 와 인증서는 "잘못돼도 당장은 돌아가는" 설정이 많다. 네 가지가 특히 그렇다.

- **이메일 검증으로 바꾸기** — 처음 발급은 똑같이 성공한다. 갱신 때 사람이
  메일을 눌러야 하는데, 아무도 그걸 기다리지 않아서 **인증서가 만료되는 날**
  알게 된다. DNS 검증은 레코드가 남아 있는 한 ACM 이 자동 갱신한다.
- **호스팅 존을 Terraform 으로 또 만들기** — apply 는 성공한다. 레지스트리가
  가리키는 NS 는 먼저 생긴 존이라, **새 존에 넣은 레코드는 아무도 보지 못한다.**
  증상이 "레코드는 있는데 조회가 안 된다"로 나와서 추적이 길다.
- **아펙스를 CNAME 으로 바꾸기** — DNS 규격 위반이라 Route 53 이 거부하거나,
  받아줘도 SOA·NS 와 충돌한다. 게다가 alias 와 달리 **조회마다 과금**된다.
- **AAAA 레코드만 추가하기** — ALB 가 `ipv4` 인데 AAAA 를 넣으면 IPv6 클라이언트가
  연결에 실패한다. 둘은 같이 바꿔야 하는데 파일이 달라서 따로 바뀐다.

범위와 한계는 `tests/terraform_support.py` 머리말 참조 — **`.tf` 소스를 읽고
실제 AWS 계정은 보지 않는다.** 콘솔에서 손으로 바꾼 레코드는 잡지 못한다.
"""

from __future__ import annotations

from typing import Any

import pytest

from tests.terraform_support import load_documents, resources

CERTIFICATE = "aws_acm_certificate"
VALIDATION = "aws_acm_certificate_validation"
RECORD = "aws_route53_record"
ZONE = "aws_route53_zone"


def listed(value: Any) -> list[Any]:
    """hcl2 는 원소가 하나면 리스트를 벗겨서 준다. 항상 리스트로 되돌린다."""
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


@pytest.fixture(scope="module")
def documents() -> list[dict[str, Any]]:
    return load_documents()


@pytest.fixture(scope="module")
def certificate(documents: list[dict[str, Any]]) -> dict[str, Any]:
    found = resources(documents, CERTIFICATE)
    assert set(found) == {"main"}, found
    return found["main"]


@pytest.fixture(scope="module")
def records(documents: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return resources(documents, RECORD)


class TestValidationStaysAutomatic:
    """규칙을 테스트가 집행한다 — 사람의 개입이 필요한 방식으로 되돌아가지 못하게."""

    def test_dns_validation_not_email(self, certificate: dict[str, Any]) -> None:
        """이메일 검증은 **갱신마다** 사람이 메일을 눌러야 한다."""
        assert certificate["validation_method"] == "DNS"

    def test_a_validation_resource_waits_for_issuance(
        self, documents: list[dict[str, Any]]
    ) -> None:
        """★ 이 자원이 없으면 `Pending validation` 인 인증서를 리스너에 붙인다."""
        assert set(resources(documents, VALIDATION)) == {"main"}

    def test_validation_records_allow_overwrite(self, records: dict[str, dict[str, Any]]) -> None:
        """인증서를 재생성하면 ACM 이 **같은 CNAME 이름**을 다시 요구한다.

        허용하지 않으면 그때 `already exists` 로 멈춘다. 덮어써도 안전한 이유는
        이 레코드의 유일한 용도가 ACM 검증이라서다.
        """
        assert records["cert_validation"]["allow_overwrite"] is True

    def test_the_wait_fails_fast_rather_than_hanging(self, documents: list[dict[str, Any]]) -> None:
        """경계값 분석 — 기본 75분이 아니라 문서상 상한인 30분을 쓴다.

        30분을 넘기면 "더 기다린다"가 아니라 "설정을 의심한다"가 맞다. 그래서
        기다리는 시간이 **진단 신호**가 되도록 상한을 당겨둔다.
        """
        timeouts = resources(documents, VALIDATION)["main"]["timeouts"]
        assert timeouts["create"] in {"10m", "15m", "20m", "30m"}, timeouts


class TestWeDoNotCreateASecondHostedZone:
    """부정 테스트 — 존을 또 만드는 경로를 막는다.

    등록 시 Route 53 이 **자동으로** 퍼블릭 존을 만든다. 우리가 또 만들면
    레코드가 아무도 보지 않는 존에 들어간다.
    """

    def test_no_hosted_zone_resource(self, documents: list[dict[str, Any]]) -> None:
        assert resources(documents, ZONE) == {}, (
            "aws_route53_zone 이 생겼다 — 도메인 등록 시 자동 생성된 존을 쓴다. "
            "존이 둘이면 레지스트리가 가리키지 않는 쪽의 레코드는 조회되지 않는다."
        )

    @pytest.mark.parametrize("name", ["cert_validation", "apex"])
    def test_records_use_the_variable_not_a_resource(
        self, records: dict[str, dict[str, Any]], name: str
    ) -> None:
        """전수 조합 — 모든 레코드가 **넘겨받은** 존 ID 를 쓴다."""
        assert records[name]["zone_id"] == "${var.hosted_zone_id}"


class TestApexPointsAtTheAlbByAlias:
    """아펙스 레코드의 형태를 고정한다."""

    def test_it_is_an_a_record(self, records: dict[str, dict[str, Any]]) -> None:
        assert records["apex"]["type"] == "A"

    def test_it_is_an_alias_not_a_cname(self, records: dict[str, dict[str, Any]]) -> None:
        """아펙스에는 CNAME 을 둘 수 없고, alias 조회는 과금되지 않는다."""
        apex = records["apex"]
        assert "alias" in apex
        assert "records" not in apex, "alias 와 records 를 같이 쓸 수 없다"
        assert "ttl" not in apex, "alias 레코드에는 TTL 을 주지 않는다"

    def test_the_alias_targets_our_alb(self, records: dict[str, dict[str, Any]]) -> None:
        alias = records["apex"]["alias"]
        assert alias["name"] == "${aws_lb.main.dns_name}"
        # ★ ALB 의 zone_id 는 **호스팅 존 ID 가 아니다.** 여기에
        #   var.hosted_zone_id 를 넣는 실수가 흔하고, 그러면 레코드가 만들어지는데
        #   아무 데도 안 간다.
        assert alias["zone_id"] == "${aws_lb.main.zone_id}"

    def test_target_health_is_evaluated(self, records: dict[str, dict[str, Any]]) -> None:
        assert records["apex"]["alias"]["evaluate_target_health"] is True


class TestIpVersionStaysConsistentAcrossFiles:
    """차등 단정 — `dns.tf` 와 `alb.tf` 가 같은 IP 버전을 말하는가.

    ★ 두 파일이 어긋나면 **한쪽만 고친 사람이 알 수 없다.** AAAA 만 있으면
    IPv6 클라이언트가 연결에 실패하고, 증상은 "일부 사용자만 안 된다"로 나온다.
    """

    def test_no_aaaa_record_while_the_alb_is_ipv4(
        self, documents: list[dict[str, Any]], records: dict[str, dict[str, Any]]
    ) -> None:
        alb_type = resources(documents, "aws_lb")["main"]["ip_address_type"]
        has_aaaa = any(record.get("type") == "AAAA" for record in records.values())
        if alb_type == "ipv4":
            assert not has_aaaa, (
                "ALB 가 ipv4 인데 AAAA 레코드가 있다 — IPv6 클라이언트가 연결에 실패한다. "
                "ALB 의 ip_address_type 을 dualstack 으로 같이 바꿔야 한다."
            )
        else:
            assert has_aaaa, (
                f"ALB 가 {alb_type} 인데 AAAA 레코드가 없다 — IPv6 주소가 쓰이지 않는다."
            )


class TestCertificateReplacementDoesNotDeadlock:
    """리스너가 쓰고 있는 인증서는 지울 수 없다."""

    def test_create_before_destroy(self, certificate: dict[str, Any]) -> None:
        """없으면 교체 시 `ResourceInUseException` 으로 apply 가 막힌다."""
        assert certificate["lifecycle"]["create_before_destroy"] is True


class TestTheCheckIsNotVacuous:
    """전제 선행 단정 — 위 검사들이 **실제로 무언가를 읽었다.**

    이 파일에도 "없음"을 확인하는 단정이 있다(존·AAAA). 파싱이 빈 결과를
    돌려주면 그 둘은 자동으로 통과한다 (JOURNAL 2026-11-19).
    """

    def test_the_certificate_was_actually_parsed(self, certificate: dict[str, Any]) -> None:
        assert certificate["domain_name"] == "${var.domain_name}"

    def test_both_records_were_parsed(self, records: dict[str, dict[str, Any]]) -> None:
        assert set(records) == {"cert_validation", "apex"}, set(records)

    def test_the_validation_record_is_driven_by_acm(
        self, records: dict[str, dict[str, Any]]
    ) -> None:
        """이름·타입·값을 우리가 정하지 않는다 — ACM 이 요구하는 것을 그대로 넣는다."""
        record = records["cert_validation"]
        assert record["name"] == "${each.value.resource_record_name}"
        assert record["type"] == "${each.value.resource_record_type}"
        assert listed(record["records"]) == ["${each.value.resource_record_value}"]
