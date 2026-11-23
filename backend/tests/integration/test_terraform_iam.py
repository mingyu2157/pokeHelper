"""워크로드 역할이 넓어지지 않는다 — 특히 `iam:PassRole`.

★ 이 파일이 막는 것 ★

IAM 정책이 **넓은 것은 장애를 일으키지 않는다.** 좁은 정책은 `apply` 나 태스크
기동이 실패해서 바로 알게 되지만, 넓은 정책은 **모든 것이 잘 돌아간다.**
그래서 사람이 못 잡는다. ADR-021 이 "좁힌 정책이 맞는지는 apply 를 돌려보는
것 말고 확인할 방법이 없다"고 적은 바로 그 지점이다 — 여기서 할 수 있는 것은
**넓어지는 방향을 막는 것**이다.

넓어져도 **평소에는 네 경우 다 아무 차이가 없다.** 차이는 사고가 났을 때 난다.

- `iam:PassRole` 의 `Resource: "*"` — 이 역할만 쥐면 **계정의 더 센 역할을
  ECS 에 넘겨** 그 권한으로 컨테이너를 띄울 수 있다. 권한 상승이 한 줄이다.
- ECR pull 이 `Resource: "*"` — 계정의 **모든** 리포지토리를 pull 할 수 있다.
- 신뢰 정책에 계정 조건이 없음 — 서비스 주체는 *모든 계정의* 그 서비스를
  뜻한다 (혼동된 대리인).
- `logs:CreateLogGroup` 이 열려 있음 — 로그는 잘 쌓인다. 보존 기간 없는
  그룹이 생겨 **비용만 조용히 오른다.**

`data.tf`(위험한 기본값)·`security_groups.tf`(ASCII 설명)와 같은 모양이다 —
없어도 안 깨지는 것을 테스트가 대신 지킨다.

범위와 한계는 `tests/terraform_support.py` 머리말 참조 — **`.tf` 소스를 읽고
실제 AWS 계정은 보지 않는다.** 콘솔에서 손으로 붙인 정책은 잡히지 않는다.
"""

from __future__ import annotations

from typing import Any

import pytest

from tests.terraform_support import blocks, data_sources, load_documents, resources

POLICY_DOCUMENT = "aws_iam_policy_document"
ROLE = "aws_iam_role"
ROLE_POLICY = "aws_iam_role_policy"

# 권한 정책 문서 셋 (신뢰 정책은 따로 본다).
PERMISSION_DOCUMENTS = ("ecs_execution", "ecs_task", "scheduler")
TRUST_DOCUMENTS = ("ecs_tasks_trust", "scheduler_trust")

# ★ `Resource: "*"` 가 **허용되는** 곳의 전체 목록.
#
#   세 곳뿐이고, 셋 다 "좁힐 수 있는데 안 좁힌 것"이 아니라 **좁힐 수 없는 것**
#   이다. 아래 TestWildcardResources 가 이 목록과 실제를 정확히 맞춰본다 —
#   새로 `*` 를 쓰면 목록에 적으면서 이유를 쓰게 된다.
ALLOWED_WILDCARDS = {
    # 레지스트리 전체에 대한 호출이라 자원 수준 권한이 없다.
    ("ecs_execution", "ECRAuthToken"),
    # ssmmessages 액션은 자원 수준 권한을 지원하지 않는다.
    ("ecs_task", "ECSExecSession"),
}


@pytest.fixture(scope="module")
def documents() -> list[dict[str, Any]]:
    return load_documents()


@pytest.fixture(scope="module")
def policies(documents: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return data_sources(documents, POLICY_DOCUMENT)


@pytest.fixture(scope="module")
def roles(documents: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return resources(documents, ROLE)


def statements(policy: dict[str, Any]) -> list[dict[str, Any]]:
    """정책 문서의 `statement` 블록 전부.

    hcl2 는 블록이 하나면 dict, 여럿이면 list 로 준다. 그 차이를 호출부가 매번
    다루면 **하나짜리를 세는 코드가 조용히 틀린다.**
    """
    return blocks(policy, "statement")


def listed(statement: dict[str, Any], key: str) -> list[Any]:
    """`actions`·`resources` 처럼 값이 하나면 스칼라로 평탄화되는 속성을 리스트로."""
    return blocks(statement, key)


def every_statement(
    policies: dict[str, dict[str, Any]], names: tuple[str, ...]
) -> list[tuple[str, dict[str, Any]]]:
    return [(name, statement) for name in names for statement in statements(policies[name])]


@pytest.fixture(scope="module")
def pass_role(policies: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """`iam:PassRole` 을 담은 statement. **하나여야 한다** — 둘로 쪼개지면
    한쪽만 좁혀도 테스트가 통과하는 상태가 된다."""
    found = [
        statement
        for _, statement in every_statement(policies, PERMISSION_DOCUMENTS)
        if "iam:PassRole" in listed(statement, "actions")
    ]
    assert len(found) == 1, f"iam:PassRole statement 가 {len(found)} 개다 (1개여야 한다)"
    return found[0]


class TestWildcardResources:
    """부정 테스트 — 넓은 `Resource: "*"` 를 **허용 목록 밖에서** 찾는다.

    목록과 실제를 양방향으로 맞춘다. 한쪽만 보면 새로 생긴 `*` 를 놓치거나,
    목록에 남은 유령 항목을 눈치채지 못한다.
    """

    def test_wildcards_match_the_allowlist_exactly(
        self, policies: dict[str, dict[str, Any]]
    ) -> None:
        found = {
            (name, statement["sid"])
            for name, statement in every_statement(policies, PERMISSION_DOCUMENTS)
            if "*" in listed(statement, "resources")
        }
        assert found == ALLOWED_WILDCARDS, (
            'Resource "*" 의 위치가 허용 목록과 다르다. 새로 추가했다면 '
            "ALLOWED_WILDCARDS 에 적고 **왜 좁힐 수 없는지** 주석을 남긴다."
        )

    def test_kms_decrypt_is_not_granted_at_all(self, policies: dict[str, dict[str, Any]]) -> None:
        """★ 넓힐 자리를 아예 없앴다 — `kms:Decrypt` 가 한 곳도 없다.

        AWS 관리형 `aws/secretsmanager` 키를 쓰면 ECS 문서 표현대로
        "required only if your secret uses a customer managed key" 다. 키 정책이
        계정 내 호출을 이미 허용하므로 IAM 에 적을 것이 없다. 적어두면 **들어갈
        곳이 없는 권한**이 하나 늘고, 그건 좁힌 게 아니라 안 쓰이는 것뿐이다.

        고객 관리형 키로 바꾸면 이 테스트가 그 사실을 알려준다 — 값을 고치기
        전에 **왜 필요해졌는지**를 쓰게 된다.
        """
        offenders = [
            (name, statement.get("sid"))
            for name, statement in every_statement(policies, PERMISSION_DOCUMENTS)
            for action in listed(statement, "actions")
            if action.startswith("kms:")
        ]
        assert offenders == [], offenders

    def test_the_auth_token_wildcard_carries_only_that_one_action(
        self, policies: dict[str, dict[str, Any]]
    ) -> None:
        """★ `*` 인 statement 에 다른 액션이 섞이면 그것들도 같이 넓어진다.

        `ecr:GetAuthorizationToken` 은 자원을 못 적지만 pull 액션들은 적을 수
        있다. 한 statement 에 묶으면 **적을 수 있었던 것까지** 계정 전체로
        열린다. 그래서 분리했고, 분리된 상태를 고정한다.
        """
        statement = self._by_sid(policies["ecs_execution"], "ECRAuthToken")
        assert listed(statement, "actions") == ["ecr:GetAuthorizationToken"]

    def test_the_exec_wildcard_carries_only_ssmmessages(
        self, policies: dict[str, dict[str, Any]]
    ) -> None:
        statement = self._by_sid(policies["ecs_task"], "ECSExecSession")
        actions = listed(statement, "actions")

        assert actions, "액션이 비어 있다"
        assert all(action.startswith("ssmmessages:") for action in actions), actions

    @staticmethod
    def _by_sid(policy: dict[str, Any], sid: str) -> dict[str, Any]:
        for statement in statements(policy):
            if statement.get("sid") == sid:
                return statement
        pytest.fail(f"sid={sid} 인 statement 가 없다")


class TestNoWildcardActions:
    """부정 테스트 — 액션 쪽이 넓어지는 경로를 막는다.

    자원을 좁혀도 액션이 `s3:*` 면 그 자원에 대해 무엇이든 할 수 있다.
    `Resource` 만 보는 리뷰가 놓치는 구멍이다.
    """

    def test_no_action_is_a_wildcard(self, policies: dict[str, dict[str, Any]]) -> None:
        offenders = [
            (name, statement.get("sid"), action)
            for name, statement in every_statement(policies, PERMISSION_DOCUMENTS)
            for action in listed(statement, "actions")
            if action == "*" or action.endswith(":*")
        ]
        assert offenders == [], offenders

    def test_no_managed_policy_is_attached(
        self, documents: list[dict[str, Any]], roles: dict[str, Any]
    ) -> None:
        """★ 관리형 정책을 붙이지 않는 것이 결정이다 (ADR-021, 2026-11-23).

        `AmazonECSTaskExecutionRolePolicy` 는 ECR·로그를 `Resource: "*"` 로 연다.
        붙이면 위 테스트들이 보는 인라인 정책은 그대로 좁은데 **실제 권한만
        넓어진다** — 테스트가 초록인 채로 의도가 사라지는 가장 나쁜 형태다.
        """
        assert resources(documents, "aws_iam_role_policy_attachment") == {}
        assert resources(documents, "aws_iam_policy") == {}

        for name, body in roles.items():
            assert "managed_policy_arns" not in body, f"{name} 에 관리형 정책이 붙어 있다"


class TestPassRoleIsNarrowed:
    """부정 테스트 — ADR-021 세부사항 7. **두 겹이 다 있어야 한다.**

    자원만 좁히면 그 역할을 ECS 아닌 서비스에 넘길 수 있고, 조건만 걸면 계정의
    모든 역할을 ECS 에 넘길 수 있다. 하나씩은 둘 다 부족하다.
    """

    def test_it_passes_only_our_two_roles(self, pass_role: dict[str, Any]) -> None:
        passable = listed(pass_role, "resources")

        assert "*" not in passable
        assert len(passable) == 2
        # 문자열이 아니라 자원 참조여야 한다 — 이름을 바꿀 때 같이 따라간다.
        assert all(target.startswith("${aws_iam_role.") for target in passable), passable

    def test_it_is_bound_to_ecs_tasks(self, pass_role: dict[str, Any]) -> None:
        conditions = blocks(pass_role, "condition")

        assert [c["variable"] for c in conditions] == ["iam:PassedToService"]
        assert listed(conditions[0], "values") == ["ecs-tasks.amazonaws.com"]

    def test_run_task_is_bound_to_our_cluster(self, policies: dict[str, dict[str, Any]]) -> None:
        """태스크 정의를 한정해도 **어느 클러스터에 띄우는지**는 아직 안 묶인다."""
        found = [
            statement
            for statement in statements(policies["scheduler"])
            if "ecs:RunTask" in listed(statement, "actions")
        ]
        assert len(found) == 1

        conditions = blocks(found[0], "condition")
        assert [c["variable"] for c in conditions] == ["ecs:cluster"]
        assert "*" not in listed(found[0], "resources")


class TestTrustPolicies:
    """신뢰 정책이 **의도한 주체만** 맡게 한다.

    권한 정책만 리뷰하면 이 층을 통째로 못 본다. 콘솔 마법사가 숨기는 부분이
    정확히 여기다 (ADR-021 맥락).
    """

    @pytest.mark.parametrize(
        ("document", "principal"),
        [
            ("ecs_tasks_trust", "ecs-tasks.amazonaws.com"),
            ("scheduler_trust", "scheduler.amazonaws.com"),
        ],
    )
    def test_only_the_intended_service_can_assume(
        self, policies: dict[str, dict[str, Any]], document: str, principal: str
    ) -> None:
        statement = statements(policies[document])
        assert len(statement) == 1

        principals = blocks(statement[0], "principals")
        assert len(principals) == 1
        assert principals[0]["type"] == "Service"
        assert listed(principals[0], "identifiers") == [principal]
        assert listed(statement[0], "actions") == ["sts:AssumeRole"]

    @pytest.mark.parametrize("document", TRUST_DOCUMENTS)
    def test_the_account_is_pinned(
        self, policies: dict[str, dict[str, Any]], document: str
    ) -> None:
        """★ 서비스 주체는 **모든 계정의** 그 서비스를 뜻한다 (혼동된 대리인).

        조건이 없으면 원리상 남의 계정 ECS·스케줄러가 우리 역할을 맡으려 시도할
        수 있다. 평소에는 아무 차이가 없어서 빠뜨려도 모른다.
        """
        conditions = blocks(statements(policies[document])[0], "condition")
        variables = [c["variable"] for c in conditions]

        assert "aws:SourceAccount" in variables, variables

    @pytest.mark.parametrize("role", ["ecs_execution", "ecs_task", "scheduler"])
    def test_every_role_uses_one_of_our_trust_documents(
        self, roles: dict[str, Any], role: str
    ) -> None:
        trust = roles[role]["assume_role_policy"]
        allowed = {f"${{data.{POLICY_DOCUMENT}.{name}.json}}" for name in TRUST_DOCUMENTS}

        assert trust in allowed, trust


class TestExecutionAndTaskRolesAreDistinct:
    """차등 단정 — 둘을 혼동했다면 **이 테스트만** 깨진다.

    ADR-021 세부사항 6 이 다루는 ECS 의 가장 흔한 함정이다. 증상은 "시크릿을 못
    읽는다"로 나타나는데 그건 거의 항상 **실행 역할** 문제인데, 이름만 보고
    태스크 역할을 고치게 된다. 어느 쪽에 무엇이 있어야 하는지를 고정한다.
    """

    def test_startup_permissions_live_on_the_execution_role(
        self, policies: dict[str, dict[str, Any]]
    ) -> None:
        actions = self._actions(policies["ecs_execution"])

        assert "secretsmanager:GetSecretValue" in actions
        assert "ecr:BatchGetImage" in actions
        assert "logs:PutLogEvents" in actions

    def test_the_task_role_has_none_of_them(self, policies: dict[str, dict[str, Any]]) -> None:
        """★ 앱이 시크릿·ECR·로그 권한을 **갖지 않는** 것이 의도다.

        컨테이너가 뜬 뒤에는 필요하지 않다 — 시크릿은 이미 환경변수로 주입돼
        있다. 여기에 있으면 "앱 코드가 시크릿을 직접 읽을 수 있는 상태"가 되고,
        그건 아무 증상도 내지 않는다.
        """
        actions = self._actions(policies["ecs_task"])

        assert not [
            action
            for action in actions
            if action.startswith(("ssm:", "kms:", "ecr:", "logs:", "secretsmanager:"))
        ], actions

    def test_only_the_app_secret_is_readable(self, policies: dict[str, dict[str, Any]]) -> None:
        """★ 계정에 시크릿이 **둘** 있고, 읽을 수 있는 건 하나뿐이어야 한다.

        `data.tf` 가 `manage_master_user_password` 로 만드는 **RDS 마스터
        비밀번호**가 같은 Secrets Manager 에 있다. 앱은 `champs_app` 으로
        붙으므로(ADR-015) 마스터를 읽을 이유가 없는데, `Resource` 를 `*` 로
        두거나 마스터 시크릿을 같이 적으면 **앱 컨테이너가 DB 마스터 비밀번호를
        읽을 수 있는 상태**가 된다. 그건 아무것도 깨뜨리지 않는다.
        """
        reads = [
            (name, statement)
            for name, statement in every_statement(policies, PERMISSION_DOCUMENTS)
            if any(a.startswith("secretsmanager:") for a in listed(statement, "actions"))
        ]
        assert len(reads) == 1, reads

        name, statement = reads[0]
        assert name == "ecs_execution"
        assert listed(statement, "resources") == ["${aws_secretsmanager_secret.app.arn}"]

    def test_creating_log_groups_is_not_granted(self, policies: dict[str, dict[str, Any]]) -> None:
        """★ 에이전트가 로그 그룹을 만들면 보존 기간이 "만료 없음"으로 생긴다.

        로그는 잘 쌓이고 아무것도 실패하지 않는다. 비용만 조용히 오른다.
        그룹은 Terraform 이 만들어 보존 기간을 강제한다.
        """
        for name in PERMISSION_DOCUMENTS:
            assert "logs:CreateLogGroup" not in self._actions(policies[name]), name

    @staticmethod
    def _actions(policy: dict[str, Any]) -> list[str]:
        return [
            action for statement in statements(policy) for action in listed(statement, "actions")
        ]


class TestNamesCarryTheEnvironment:
    """역할 이름이 환경을 담는다 — `staging` 을 만들면 이름이 충돌한다.

    IAM 역할 이름은 **계정 전역**이고 리전별이 아니다. 접두사를 빼면 두 번째
    환경의 `apply` 가 EntityAlreadyExists 로 죽는다.
    """

    @pytest.mark.parametrize("role", ["ecs_execution", "ecs_task", "scheduler"])
    def test_the_role_name_is_prefixed(self, roles: dict[str, Any], role: str) -> None:
        assert roles[role]["name"].startswith("${local.name}-")


class TestTheCheckIsNotVacuous:
    """전제 선행 단정 — 위 검사들이 **실제로 무언가를 읽었다.**

    ★ 이 파일의 거의 모든 단정이 "없음"을 확인하는 형태다. 파싱이 빈 결과를
    돌려주면 `AdministratorAccess` 도 없고 `*` 도 없고 넓은 액션도 없다 —
    전부 초록인데 검증한 것은 아무것도 없다. 그래서 읽은 양을 따로 단정한다
    (JOURNAL 2026-11-19 에 같은 함정을 한 번 밟았다).
    """

    def test_all_five_policy_documents_were_parsed(
        self, policies: dict[str, dict[str, Any]]
    ) -> None:
        assert set(policies) >= set(PERMISSION_DOCUMENTS) | set(TRUST_DOCUMENTS)

    def test_three_roles_and_three_inline_policies_exist(
        self, documents: list[dict[str, Any]], roles: dict[str, Any]
    ) -> None:
        assert set(roles) == {"ecs_execution", "ecs_task", "scheduler"}
        assert set(resources(documents, ROLE_POLICY)) == {"ecs_execution", "ecs_task", "scheduler"}

    def test_every_statement_actually_has_actions_and_resources(
        self, policies: dict[str, dict[str, Any]]
    ) -> None:
        for name, statement in every_statement(policies, PERMISSION_DOCUMENTS):
            assert listed(statement, "actions"), (name, statement)
            assert listed(statement, "resources"), (name, statement)

    def test_interpolation_survived_the_parser(self, policies: dict[str, dict[str, Any]]) -> None:
        """★ 자원 참조가 문자열로 납작해지지 않았다.

        `${aws_iam_role...}` 가 파서에서 사라지면 "자원 참조로 쓴다"는 단정이
        **공회전한다.** 참조가 실제로 남아 있는 것을 확인한다.
        """
        referenced = [
            target
            for _, statement in every_statement(policies, PERMISSION_DOCUMENTS)
            for target in listed(statement, "resources")
            if isinstance(target, str) and target.startswith("${")
        ]
        assert len(referenced) >= 3, referenced
