"""시크릿 **값**이 코드·plan·state 에 들어오지 않는다 (ADR-022).

★ 이 파일이 막는 것 ★

`secrets.tf` 의 설계는 **없는 것**으로 이루어져 있다 — `aws_secretsmanager_secret`
은 있고 `aws_secretsmanager_secret_version` 은 **없다.** 값이 어디에도 저장되지
않는 이유가 그 부재 하나다.

그래서 깨뜨리기가 아주 쉽다. 누군가 `terraform apply` 한 번으로 값을 넣고
싶어서 version 자원을 추가하면:

- 모든 것이 잘 돌아간다. 앱은 뜨고 테스트는 초록이다.
- 그런데 **값이 state 에 평문으로 들어간다.** state 버킷은 버전 관리가 켜져
  있어서(`bootstrap/main.tf`) 구 버전에도 영구히 남는다. 지우기가 더 어렵다.
- `.tf` 를 커밋하면 저장소에도 남는다.

증상이 없고, 되돌리기는 어렵고, 리뷰에서는 "값을 채웠다"로 보인다. 그래서
사람이 아니라 테스트가 지킨다.

같은 이유로 SSM 파라미터 스토어를 쓰지 않았다 — `ignore_changes = [value]` 는
plan 시점의 동작만 바꾸고 `refresh` 가 state 를 실제 값으로 다시 쓴다.
"값을 코드에 안 넣는다"는 지켜지지만 "state 에 안 들어간다"는 지켜지지 않는다.

범위와 한계는 `tests/terraform_support.py` 머리말 참조 — **`.tf` 소스를 읽고
실제 AWS 계정은 보지 않는다.** 실제 값이 들어갔는지는 여기서 알 수 없고,
빠뜨리면 첫 태스크 기동이 시크릿을 못 가져와 **소리 내며** 실패한다.
"""

from __future__ import annotations

import re
from typing import Any

import pytest

from tests.terraform_support import load_documents, local_values, resources, terraform_dir

SECRET = "aws_secretsmanager_secret"
SECRET_VERSION = "aws_secretsmanager_secret_version"

# 컨테이너에 주입될 환경변수 이름 전체. 하나라도 빠지면 그 설정이 운영에서
# **기본값으로** 돈다 (ADR-019 가 막으려던 것이 정확히 그것이다).
EXPECTED_KEYS = {
    "DATABASE_URL",
    "MIGRATION_DATABASE_URL",
    "METRICS_TOKEN",
    "DATA_SOURCE_NAME",
    "GF_SECURITY_ADMIN_USER",
    "GF_SECURITY_ADMIN_PASSWORD",
}

ENV_VAR_NAME = re.compile(r"^[A-Z][A-Z0-9_]*$")


@pytest.fixture(scope="module")
def documents() -> list[dict[str, Any]]:
    return load_documents()


@pytest.fixture(scope="module")
def secrets(documents: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return resources(documents, SECRET)


@pytest.fixture(scope="module")
def secret_keys(documents: list[dict[str, Any]]) -> dict[str, str]:
    """`secrets.tf` 의 `local.app_secret_keys`."""
    locals_ = local_values(documents)
    assert "app_secret_keys" in locals_, "secrets.tf 에서 local.app_secret_keys 를 찾지 못했다"
    return locals_["app_secret_keys"]


class TestTheValueNeverEntersTerraform:
    """★ 이 클래스가 ADR-022 의 전부다.

    부정 테스트 — **값을 Terraform 에 넣는 모든 경로**를 막는다. 한 경로만
    열려도 state 에 평문이 남고, 남으면 버전 관리 때문에 되돌리기 어렵다.
    """

    def test_no_secret_version_resource_exists(self, documents: list[dict[str, Any]]) -> None:
        """값을 담는 자원은 `aws_secretsmanager_secret_version` 뿐이다. 없어야 한다."""
        assert resources(documents, SECRET_VERSION) == {}, (
            "aws_secretsmanager_secret_version 이 생겼다 — 값이 state 에 평문으로 들어간다. "
            "값은 `aws secretsmanager put-secret-value` 로 손으로 넣는다 (README 5-C)."
        )

    def test_no_secret_string_anywhere_in_the_tf_sources(self) -> None:
        """★ `secret_string` 이라는 글자 자체가 `.tf` 에 없어야 한다.

        위 테스트는 자원 **타입**을 보지만, `aws_secretsmanager_secret` 쪽에
        값을 넣는 다른 인자가 생기거나 `moved`·`import` 블록으로 우회할 수도
        있다. 소스 전체를 한 번 더 훑는다 — 가장 둔하지만 가장 넓게 잡는다.
        """
        offenders = []
        for path in sorted(terraform_dir().glob("*.tf")):
            text = path.read_text(encoding="utf-8")
            for number, line in enumerate(text.splitlines(), start=1):
                stripped = line.lstrip()
                if stripped.startswith("#"):
                    continue  # 주석에서 설명하는 것은 허용한다
                if "secret_string" in line:
                    offenders.append(f"{path.name}:{number}")
        assert offenders == [], offenders

    def test_no_ssm_parameter_holds_a_value(self, documents: list[dict[str, Any]]) -> None:
        """★ SSM 으로 되돌아가는 것도 막는다.

        `aws_ssm_parameter` 는 `value` 가 **필수**라 값 없이 만들 수 없고,
        `ignore_changes` 는 `refresh` 를 막지 못한다. 그래서 이 자원 타입
        자체가 ADR-022 가 기각한 경로다.
        """
        assert resources(documents, "aws_ssm_parameter") == {}


class TestOneSecretWithAllTheKeys:
    """시크릿은 하나고, 키 목록이 실제 필요한 것과 일치한다.

    6개를 따로 만들면 월 $2.40, 하나에 JSON 으로 담으면 $0.40 다. ECS 는
    `valueFrom` 끝에 JSON 키를 붙여 키 하나만 꺼낼 수 있다.
    """

    def test_exactly_one_secret(self, secrets: dict[str, dict[str, Any]]) -> None:
        assert set(secrets) == {"app"}

    def test_the_keys_are_the_ones_the_app_needs(self, secret_keys: dict[str, str]) -> None:
        assert set(secret_keys) == EXPECTED_KEYS

    @pytest.mark.parametrize("key", sorted(EXPECTED_KEYS))
    def test_each_key_is_a_valid_env_var_name(self, secret_keys: dict[str, str], key: str) -> None:
        """★ 키 이름 = 환경변수 이름이라는 규약을 고정한다.

        이게 성립하면 `ecs.tf` 의 매핑이 `name = key` 로 끝난다. 규약이 깨지면
        매핑 표를 손으로 관리해야 하고, 거기서 틀리면 **앱이 기본값으로 조용히
        뜬다** — 예를 들어 `METRICS_TOKEN` 이 안 주입되면 운영 기동이 거부되지만
        (ADR-019), `CORS_ORIGINS` 류는 그냥 기본값으로 돈다.
        """
        assert ENV_VAR_NAME.match(key), key

    def test_every_key_is_documented(self, secret_keys: dict[str, str]) -> None:
        """값을 손으로 넣는 사람이 "이게 뭔지" 읽을 수 있어야 한다."""
        for key, description in secret_keys.items():
            assert isinstance(description, str) and description.strip(), key


class TestDestroyAndApplyCanRoundTrip:
    """★ `destroy` 후 같은 이름으로 다시 `apply` 가 된다.

    Secrets Manager 는 기본 복구 기간이 30일이고, 그 동안 **같은 이름을 다시
    만들 수 없다** ("already scheduled for deletion"). 최소값도 7일이라
    그 기간 내내 재구축이 불가능하다. `ecr.tf` 의 `force_delete` 와 같은
    판단으로 0(즉시 삭제)을 골랐다 — IaC 왕복이 성립하는 쪽이 더 중요하다.

    대신 **되돌릴 수 없다.** 사람이 넣은 값이라 다시 넣을 수 있고, 데이터가
    아니라는 전제에 기대고 있다. 그 전제가 코드에 보이도록 고정한다.
    """

    def test_the_recovery_window_is_zero(self, secrets: dict[str, dict[str, Any]]) -> None:
        assert secrets["app"]["recovery_window_in_days"] == 0

    def test_it_is_a_number_not_a_string(self, secrets: dict[str, dict[str, Any]]) -> None:
        """전제 선행 단정 — `"0"` 이면 위 비교가 통째로 빗나간다."""
        assert isinstance(secrets["app"]["recovery_window_in_days"], int)


class TestNoCustomerManagedKey:
    """고객 관리형 KMS 키를 쓰지 않는다 — 월 $1 이고, 쓸 이유가 아직 없다.

    이 선택이 `iam.tf` 와 묶여 있다. AWS 관리형 키면 실행 역할에 `kms:Decrypt`
    가 **필요 없고**, 그래서 그 권한을 주지 않았다. 여기서 고객 관리형 키로
    바꾸면 **IAM 을 같이 고쳐야 하는데 증상은 "시크릿을 못 가져온다"로 나오고
    메시지는 KMS 를 가리킨다.** 그 연결을 테스트에 남긴다.
    """

    def test_no_kms_key_is_pinned(self, secrets: dict[str, dict[str, Any]]) -> None:
        assert "kms_key_id" not in secrets["app"]


class TestTheCheckIsNotVacuous:
    """전제 선행 단정 — 위 검사들이 **실제로 무언가를 읽었다.**

    이 파일의 단정은 거의 전부 "없음"을 확인하는 형태다. 파싱이 빈 결과를
    돌려주면 version 자원도 없고 `secret_string` 도 없고 SSM 파라미터도 없다 —
    전부 초록인데 검증한 것은 아무것도 없다 (JOURNAL 2026-11-19).
    """

    def test_the_secret_resource_was_actually_parsed(
        self, secrets: dict[str, dict[str, Any]]
    ) -> None:
        assert "app" in secrets
        assert secrets["app"]["name"] == "${local.name}/app"

    def test_the_key_map_is_not_empty(self, secret_keys: dict[str, str]) -> None:
        assert len(secret_keys) == len(EXPECTED_KEYS) == 6

    def test_the_tf_sweep_actually_read_files(self) -> None:
        """`secret_string` 검사가 파일을 하나도 못 열었으면 그것도 '없음'이 된다."""
        files = list(terraform_dir().glob("*.tf"))
        assert len(files) >= 7, [path.name for path in files]
        assert any(path.name == "secrets.tf" for path in files)
