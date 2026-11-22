"""상태 저장소가 인터넷에서 닿지 않고, 비밀이 코드에 없고, DR 약속이 켜져 있다.

★ 이 파일이 막는 것 ★

`data.tf` 의 위험한 설정들은 **켜져 있어도 아무것도 안 깨진다.** DB 는 멀쩡히
붙고, 앱은 돌고, 테스트도 전부 초록이다. 그래서 사람이 못 잡는다.

- `publicly_accessible = true` 로 떠도 **평소에는 아무 차이가 없다.**
  보안그룹이 막아주기 때문이다. 보안그룹 규칙이 한 번 느슨해지는 날 터진다.
- `backup_retention_period = 0` 이면 자동 백업이 꺼지고 **PITR 이 불가능해진다.**
  그런데 그 사실은 **복구해야 하는 날에** 알게 된다. ADR-020 의 RPO 5분이
  거기에 기대고 있다.
- `skip_final_snapshot = true` 면 `destroy` 가 **조용히 성공하고 데이터가
  사라진다.** 실패하지 않기 때문에 더 나쁘다.
- 비밀번호가 `.tf` 에 박히면 **state 와 plan 출력에 평문으로 남는다.**

ADR-013(출처 표기)·ADR-015(DB 권한)·ADR-020 세부사항 8(예산)과 같은 모양이다 —
없어도 안 깨지는 것을 테스트가 대신 지킨다.

범위와 한계는 `tests/terraform_support.py` 머리말 참조 — **`.tf` 소스를 읽고
실제 AWS 계정은 보지 않는다.** 콘솔에서 손으로 고친 것은 다음 `apply` 전까지
잡히지 않는다.
"""

from __future__ import annotations

from typing import Any

import pytest

from tests.terraform_support import load_documents, resources

DB = "aws_db_instance"
CACHE = "aws_elasticache_replication_group"
DB_SUBNETS = "aws_db_subnet_group"
CACHE_SUBNETS = "aws_elasticache_subnet_group"


@pytest.fixture(scope="module")
def documents() -> list[dict[str, Any]]:
    return load_documents()


@pytest.fixture(scope="module")
def databases(documents: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return resources(documents, DB)


@pytest.fixture(scope="module")
def caches(documents: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return resources(documents, CACHE)


class TestNothingIsReachableFromTheInternet:
    """★ 방어선이 둘이고, **둘 다** 지켜야 한다.

    "프라이빗 서브넷에 뒀다"와 "퍼블릭 접근을 껐다"는 **다른 설정**이다. 하나만
    지키면 의도와 다른 상태가 되는데 평소에는 아무 차이가 없어서 안 보인다.
    그래서 두 개를 따로 검사한다 (ADR-020 결정 1 이 퍼블릭 서브넷을 골랐으므로
    여기서 느슨해지면 실제로 노출된다).
    """

    def test_the_database_is_not_publicly_accessible(
        self, databases: dict[str, dict[str, Any]]
    ) -> None:
        for name, body in databases.items():
            assert body.get("publicly_accessible") is False, (
                f"{name}: publicly_accessible 이 명시적으로 false 여야 한다 "
                f"(지금: {body.get('publicly_accessible')!r}). "
                "기본값에 맡기지 않는다 — 기본값은 버전에 따라 바뀐다"
            )

    @pytest.mark.parametrize("kind", [DB_SUBNETS, CACHE_SUBNETS])
    def test_subnet_groups_use_private_subnets_only(
        self, documents: list[dict[str, Any]], kind: str
    ) -> None:
        """서브넷 그룹이 퍼블릭 서브넷을 참조하지 않는다.

        ★ 한계: hcl2 는 `subnet_ids` 를 **식 문자열 그대로** 돌려준다
        (`${[for subnet in aws_subnet.private : subnet.id]}`). 그래서 이것은
        식을 평가한 검사가 아니라 **참조하는 이름을 보는 검사**다. 그래도
        `aws_subnet.public` 이 들어오는 사고는 잡는다.
        """
        groups = resources(documents, kind)
        assert groups, f"{kind} 가 없다"

        for name, body in groups.items():
            expression = str(body.get("subnet_ids", ""))
            assert "aws_subnet.private" in expression, (
                f"{name}: 프라이빗 서브넷을 참조하지 않는다 ({expression!r})"
            )
            assert "aws_subnet.public" not in expression, (
                f"{name}: ★ 퍼블릭 서브넷을 참조한다 ({expression!r})"
            )


class TestSecretsAreNotInTheCode:
    """★ `.tf` 에 적은 비밀은 **state 와 plan 출력에 평문으로** 남는다.

    state 는 S3 에서 암호화되지만, "암호화된 곳에 평문이 있다"와 "평문이 없다"는
    다르다. README 0장이 "시크릿 값은 Terraform 에 넣지 않는다"를 요구한다.
    """

    def test_the_master_password_is_managed_by_rds(
        self, databases: dict[str, dict[str, Any]]
    ) -> None:
        """RDS 가 비밀번호를 만들어 Secrets Manager 에 넣는다.

        Terraform 이 값을 보지 못하므로 state 에 남을 수가 없다.
        """
        for name, body in databases.items():
            assert body.get("manage_master_user_password") is True, (
                f"{name}: manage_master_user_password 가 켜져 있어야 한다. "
                "직접 비밀번호를 만들면 그 값이 state 에 평문으로 들어간다"
            )

    def test_no_password_attribute_is_set(self, databases: dict[str, dict[str, Any]]) -> None:
        """`password` 를 같이 적으면 안 된다.

        ★ RDS 관리 비밀번호와 `password` 는 **동시에 쓸 수 없다**. 하나를 켜고
        다른 하나를 적으면 apply 가 거부된다. 그보다 중요한 건, `password` 가
        적혀 있다는 것 자체가 **값이 코드나 변수로 들어왔다**는 뜻이라는 것이다.
        """
        for name, body in databases.items():
            assert "password" not in body, (
                f"{name}: password 를 적지 않는다 (지금: {body.get('password')!r})"
            )

    def test_the_cache_has_no_literal_auth_token(self, caches: dict[str, dict[str, Any]]) -> None:
        """AUTH 토큰을 쓰지 않기로 했다. 쓰려면 값을 state 밖에 둬야 한다.

        근거는 `data.tf` 주석: 막는 대상이 이미 프라이빗 서브넷 + 보안그룹으로
        막혀 있고, 토큰을 쓰면 그 값이 state 에 평문으로 들어온다. 나중에 필요
        해지면 ElastiCache IAM 인증으로 간다 — 그쪽은 저장할 비밀이 없다.
        """
        for name, body in caches.items():
            token = body.get("auth_token")
            if token is None:
                continue
            assert "var." in str(token) or "random_password" in str(token), (
                f"{name}: auth_token 이 코드에 박혀 있다 ({token!r})"
            )


class TestEncryptionIsOn:
    """저장·전송 양쪽을 검사한다. 하나만 켜는 것은 흔한 실수다."""

    def test_database_storage_is_encrypted(self, databases: dict[str, dict[str, Any]]) -> None:
        for name, body in databases.items():
            assert body.get("storage_encrypted") is True, f"{name}: 저장 암호화가 꺼져 있다"

    def test_cache_encrypts_at_rest_and_in_transit(self, caches: dict[str, dict[str, Any]]) -> None:
        for name, body in caches.items():
            assert body.get("at_rest_encryption_enabled") is True, (
                f"{name}: 저장 암호화가 꺼져 있다"
            )
            assert body.get("transit_encryption_enabled") is True, (
                f"{name}: 전송 암호화가 꺼져 있다. "
                "★ 켜면 접속 URL 이 rediss:// 여야 한다 — ecs.tf 에서 맞춘다"
            )


class TestDrPromisesAreKept:
    """★ ADR-020 결정 3 의 RPO 5분 / RTO 1시간이 **설정에 실제로 있는가.**

    문서에 RPO 를 적어두고 자동 백업이 꺼져 있으면, 그 문서는 거짓이다.
    그리고 그 거짓은 **복구해야 하는 날에** 발견된다.
    """

    def test_automated_backup_is_enabled(self, databases: dict[str, dict[str, Any]]) -> None:
        """보존 기간이 변수에서 오고, 0 이 아니다.

        0 이면 자동 백업이 꺼지고 **PITR 이 불가능**해진다. 변수 쪽에
        `>= 1` validation 이 있으므로, 여기서는 "변수를 쓰는가"를 본다 —
        숫자를 직접 박으면 그 validation 을 우회한다.
        """
        for name, body in databases.items():
            retention = str(body.get("backup_retention_period", ""))
            assert "var." in retention, (
                f"{name}: backup_retention_period 가 변수가 아니다 ({retention!r}). "
                "직접 박으면 변수의 >= 1 검증을 우회한다"
            )

    def test_deletion_protection_is_on(self, databases: dict[str, dict[str, Any]]) -> None:
        for name, body in databases.items():
            assert body.get("deletion_protection") is True, (
                f"{name}: deletion_protection 이 꺼져 있다. "
                "운영 DB 는 destroy 가 한 번에 통과해서는 안 된다"
            )

    def test_the_final_snapshot_is_a_pair(self, databases: dict[str, dict[str, Any]]) -> None:
        """★ 두 설정이 **쌍**이다. 하나만 지키면 조용히 틀린다.

        `skip_final_snapshot = false` 만 두고 이름을 안 주면 apply/destroy 가
        거부된다(시끄러운 실패, 괜찮다). 반대로 `true` 면 **destroy 가 조용히
        성공하고 데이터가 사라진다** — 이쪽이 위험하다. 그래서 쌍으로 검사한다.
        """
        for name, body in databases.items():
            assert body.get("skip_final_snapshot") is False, (
                f"{name}: skip_final_snapshot 이 true 면 destroy 가 조용히 데이터를 지운다"
            )
            assert body.get("final_snapshot_identifier"), (
                f"{name}: skip_final_snapshot=false 인데 스냅샷 이름이 없다 — apply 가 거부된다"
            )


class TestCostGuards:
    """ADR-020 의 비용표와 직접 연결된 값들. 바뀌면 월 청구액이 바뀐다."""

    def test_the_database_is_single_az(self, databases: dict[str, dict[str, Any]]) -> None:
        """Multi-AZ 는 **요금이 두 배**다 ($0.025 -> $0.051/시간).

        가용성보다 비용을 골랐고(ADR-020 결정 3), 대신 DR 을 백업·복구로 지킨다.
        바꿀 때는 ADR 을 먼저 고친다.
        """
        for name, body in databases.items():
            assert body.get("multi_az") is False, (
                f"{name}: multi_az 가 켜졌다. 월 비용이 약 $18 늘어난다 — "
                "ADR-020 결정 3 을 먼저 고친다"
            )

    def test_the_cache_is_a_single_node(self, caches: dict[str, dict[str, Any]]) -> None:
        """복제본 하나가 월 $18 을 더한다. 캐시는 잃어도 다시 계산하면 된다."""
        for name, body in caches.items():
            assert body.get("num_cache_clusters") == 1, (
                f"{name}: 노드가 1개가 아니다 ({body.get('num_cache_clusters')!r}). "
                "복제본 하나당 월 약 $18"
            )

    @pytest.mark.parametrize(
        ("kind", "attribute"),
        [(DB, "instance_class"), (CACHE, "node_type")],
    )
    def test_sizes_come_from_variables(
        self, documents: list[dict[str, Any]], kind: str, attribute: str
    ) -> None:
        """크기를 코드에 박으면 ADR-020 의 비용표와 연결이 끊긴다."""
        for name, body in resources(documents, kind).items():
            value = str(body.get(attribute, ""))
            assert "var." in value, f"{name}: {attribute} 가 변수가 아니다 ({value!r})"


class TestTheCheckIsNotVacuous:
    """전제 선행 단정 — 검사 대상이 실제로 **있었다**.

    파싱이 어긋나 빈 딕셔너리가 나오면 위 단정들이 전부 공짜로 통과한다
    (TEST_STRATEGY 3-9). 같은 함정을 두 번 겪었다 — `.tf` 파서의 따옴표 문제와,
    401 응답을 "지표가 없다"로 읽은 교차검증.
    """

    def test_both_data_stores_were_parsed(
        self, databases: dict[str, dict[str, Any]], caches: dict[str, dict[str, Any]]
    ) -> None:
        assert databases, "aws_db_instance 를 하나도 못 읽었다 — 파싱 문제일 수 있다"
        assert caches, "aws_elasticache_replication_group 을 하나도 못 읽었다"

    def test_booleans_really_came_through_as_booleans(
        self, databases: dict[str, dict[str, Any]]
    ) -> None:
        """★ 위 검사들이 `is False` / `is True` 로 **동일성**을 본다.

        파서가 `true` 를 문자열 `"true"` 로 주면 `is True` 가 전부 실패한다 —
        그건 괜찮다(시끄러운 실패). 하지만 `is False` 쪽은 **속성이 아예 없을
        때도** 실패하지 않는 형태로 쓰기 쉬워서, 타입이 바뀌었는지 여기서 한 번
        확인한다. 파서 버전을 올렸을 때 조용히 공회전하는 것을 막는다.
        """
        for name, body in databases.items():
            assert isinstance(body.get("multi_az"), bool), (
                f"{name}: multi_az 가 bool 이 아니다 ({body.get('multi_az')!r}) — "
                "파서가 bool 을 문자열로 주기 시작했다면 이 파일의 단정들을 다시 본다"
            )
