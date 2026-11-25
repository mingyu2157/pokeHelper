"""ECS 구성이 **조용히 깨지는 방향**을 막는다 (`infra/terraform/ecs.tf`).

★ 이 파일이 막는 것 ★

ECS 는 "틀렸는데 서비스가 멀쩡히 도는" 설정이 유독 많다. 여섯 가지가 특히 그렇다.

- **클러스터 이름이 어긋나기** — `iam.tf` 의 스케줄러 정책이 클러스터 ARN 을
  **이름 규칙으로 짜맞춘다** (ecs.tf 가 없던 시점에 정책을 먼저 썼기 때문이다).
  이름을 바꾸면 ECS 는 정상이고 **스케줄러만** RunTask AccessDenied 로 막히는데,
  메시지가 클러스터 이름을 가리키지 않는다. 그래서 두 파일을 교차검증한다.
- **시크릿을 평문 `environment` 로 옮기기** — 앱은 똑같이 뜬다. 다만 그 값이
  `describe-task-definition` 과 콘솔에 **그대로 보인다.** ADR-022 가 state 에서
  비밀을 치운 노력이 여기서 한 줄로 무효가 된다.
- **앱 컨테이너에 `MIGRATION_DATABASE_URL` 을 주기** — 아무 증상이 없다.
  DDL 권한을 가진 유일한 계정(ADR-015)의 자격증명을 앱 프로세스가 들고 도는
  상태가 되고, `config.py` 가 직접 적어둔 분리가 무의미해진다.
- **`REDIS_URL` 을 `redis://` 로 고치기** — s 가 하나냐 둘이냐의 차이다.
  `data.tf` 가 전송 암호화를 켜뒀으므로 TLS 로 붙지 않으면 연결이 거부된다.
- **`TRUST_PROXY_HEADERS` 를 끄기** — 속도 제한이 **계속 동작한다.** 다만 모든
  요청이 ALB 의 IP 하나로 보여서 **전체 사용자가 한 덩어리로 제한된다.**
  부하가 적을 때는 아무도 모른다.
- **로그 그룹 보존 기간을 빼기** — 로그는 정상적으로 쌓인다. 영원히 쌓인다.

범위와 한계는 `tests/terraform_support.py` 머리말 참조 — **`.tf` 소스를 읽고
실제 AWS 계정은 보지 않는다.** 콘솔에서 손으로 바꾼 설정은 잡지 못한다.
"""

from __future__ import annotations

from typing import Any

import pytest

from tests.terraform_support import (
    clean,
    data_sources,
    load_documents,
    local_values,
    resources,
    unquote,
    variables,
)

CLUSTER = "aws_ecs_cluster"
TASK_DEFINITION = "aws_ecs_task_definition"
SERVICE = "aws_ecs_service"
LOG_GROUP = "aws_cloudwatch_log_group"

EXPECTED_TASK_DEFINITIONS = {"backend", "frontend", "migrate", "ingest"}
EXPECTED_SERVICES = {"backend", "frontend"}

# `local.name` 은 main.tf 에서 `${var.project}-${var.environment}` 로 조립된다.
# hcl2 는 참조를 그대로 문자열로 주므로 이 토큰으로 비교한다.
NAME_REF = "${local.name}"


def listed(value: Any) -> list[Any]:
    """hcl2 는 원소가 하나면 리스트를 벗겨서 준다. 항상 리스트로 되돌린다."""
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def dequote_keys(mapping: Any) -> dict[str, Any]:
    """★ 객체 키의 따옴표를 벗긴다.

    `awslogs-group` 처럼 하이픈이 든 키는 HCL 객체 생성자에서 **따옴표가
    필수**이고, hcl2 는 그 따옴표를 키에 남긴다(`'"awslogs-group"'`).
    정규화하지 않으면 **모든 조회가 조용히 빗나가서** 검사가 공회전한다
    (`terraform_support.unquote` 가 값에 대해 하는 일을 키에 대해 한다).
    """
    raw = clean(mapping)
    assert isinstance(raw, dict), raw
    return {unquote(key): value for key, value in raw.items()}


@pytest.fixture(scope="module")
def documents() -> list[dict[str, Any]]:
    return load_documents()


@pytest.fixture(scope="module")
def locals_(documents: list[dict[str, Any]]) -> dict[str, Any]:
    return local_values(documents)


@pytest.fixture(scope="module")
def task_definitions(documents: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return resources(documents, TASK_DEFINITION)


@pytest.fixture(scope="module")
def services(documents: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return resources(documents, SERVICE)


@pytest.fixture(scope="module")
def environment(locals_: dict[str, Any]) -> dict[str, Any]:
    """평문 환경변수. 세 태스크 정의가 같은 맵을 공유한다."""
    return clean(locals_["common_environment"])


@pytest.fixture(scope="module")
def image_tag(documents: list[dict[str, Any]]) -> dict[str, Any]:
    found = variables(documents)
    assert "image_tag" in found, sorted(found)
    return found["image_tag"]


@pytest.fixture(scope="module")
def containers(locals_: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """컨테이너 정의는 `locals` 에 있다 — 그게 조건이었다.

    리소스 안에 `jsonencode({...})` 로 쓰면 hcl2 가 전체를 문자열 하나로
    돌려줘서 **안을 들여다볼 수 없다.** `iam.tf` 가 정책을 data 소스로 쓴 것과
    같은 이유다.
    """
    return {
        "backend": locals_["backend_container"],
        "frontend": locals_["frontend_container"],
        "migrate": locals_["migrate_container"],
        "ingest": locals_["ingest_container"],
    }


class TestClusterNameMatchesWhatIamAssumes:
    """차등 단정 — `iam.tf` 와 `ecs.tf` 의 두 이름을 서로 대조한다.

    한 파일만 보면 둘 다 옳아 보인다. 어긋났을 때 깨지는 것은 ECS 가 아니라
    스케줄러이고, 그쪽은 매일 06:00 에만 돈다.
    """

    def test_cluster_is_named_after_local_name(self, documents: list[dict[str, Any]]) -> None:
        cluster = resources(documents, CLUSTER)
        assert set(cluster) == {"main"}, cluster
        assert cluster["main"]["name"] == NAME_REF

    def test_iam_builds_the_same_cluster_arn(self, locals_: dict[str, Any]) -> None:
        """★ 정책이 가정하는 ARN 의 꼬리가 클러스터 이름과 같아야 한다."""
        assert f":cluster/{NAME_REF}" in locals_["ecs_cluster_arn"]

    def test_container_insights_is_explicitly_off(self, documents: list[dict[str, Any]]) -> None:
        """계정 수준 기본값이 켜져 있을 수 있다. 적지 않으면 '우리가 끈 것'과
        '기본값이 꺼져 있던 것'을 구분할 수 없고, 켜지면 지표가 과금된다."""
        setting = clean(resources(documents, CLUSTER)["main"]["setting"])
        assert setting["name"] == "containerInsights"
        assert setting["value"] == "disabled"


class TestTaskFamiliesMatchTheSchedulerPattern:
    """차등 단정 — 패밀리 접두사와 IAM 자원 패턴을 대조한다."""

    def test_every_task_definition_exists(
        self, task_definitions: dict[str, dict[str, Any]]
    ) -> None:
        assert set(task_definitions) == EXPECTED_TASK_DEFINITIONS

    @pytest.mark.parametrize("key", sorted(EXPECTED_TASK_DEFINITIONS))
    def test_family_starts_with_local_name(
        self, task_definitions: dict[str, dict[str, Any]], key: str
    ) -> None:
        assert task_definitions[key]["family"].startswith(f"{NAME_REF}-")

    def test_iam_pattern_covers_that_prefix(self, locals_: dict[str, Any]) -> None:
        """★ 접두사를 바꾸면 스케줄러가 자기 태스크를 띄울 수 없게 된다."""
        assert f"task-definition/{NAME_REF}-*:*" in locals_["scheduled_task_definition_arn"]


class TestSecretsNeverBecomePlaintext:
    """부정 테스트 — 비밀이 평문 `environment` 로 새는 경로를 막는다.

    ADR-022 는 값이 state 에 들어가지 않게 했다. 그 값을 평문 환경변수로
    옮기면 `describe-task-definition` 으로 누구나 읽을 수 있어 **같은 노출이
    다른 문으로 일어난다.**
    """

    def test_no_secret_key_appears_in_plaintext_environment(self, locals_: dict[str, Any]) -> None:
        secret_keys = set(listed(locals_["backend_secret_keys"])) | set(
            listed(locals_["migrate_secret_keys"])
        )
        plaintext = set(clean(locals_["common_environment"]))
        assert secret_keys & plaintext == set(), secret_keys & plaintext

    def test_plaintext_environment_never_references_the_secret(
        self, locals_: dict[str, Any]
    ) -> None:
        """시크릿 ARN 을 평문 쪽에서 참조하면 주입이 아니라 노출이 된다."""
        for key, value in clean(locals_["common_environment"]).items():
            assert "secretsmanager" not in str(value), key
            assert "app_secret_arn" not in str(value), key

    @pytest.mark.parametrize("key", ["backend", "migrate"])
    def test_secrets_pull_one_json_key_each(
        self, containers: dict[str, dict[str, Any]], key: str
    ) -> None:
        """`valueFrom` 끝의 `:<키>::` 가 JSON 에서 그 키 하나만 꺼낸다.

        접미사를 빼면 **JSON 전체가 환경변수 하나에 들어간다** — 앱은
        `DATABASE_URL` 자리에서 JSON 을 받고 접속에 실패하는데, 메시지는
        DSN 형식 오류라서 원인이 드러나지 않는다.
        """
        expression = containers[key]["secrets"]
        assert "valueFrom" in expression
        assert "::" in expression

    def test_frontend_holds_no_secret_at_all(self, containers: dict[str, dict[str, Any]]) -> None:
        """프론트는 DB·Redis·토큰을 모른다. 전부 백엔드를 거친다."""
        assert "secrets" not in containers["frontend"]


class TestAppDoesNotGetTheDdlCredential:
    """규칙을 테스트가 집행한다 — ADR-015 의 계정 분리.

    `app/core/config.py` 가 직접 적어뒀다: "런타임 코드가 이 값을 읽으면
    분리의 의미가 없어진다 — alembic/env.py 만 읽는다". 문서는 읽는 사람에게만
    작동한다.
    """

    def test_backend_has_no_migration_dsn(self, locals_: dict[str, Any]) -> None:
        assert "MIGRATION_DATABASE_URL" not in listed(locals_["backend_secret_keys"])

    def test_migrate_task_does_have_it(self, locals_: dict[str, Any]) -> None:
        """없으면 `alembic upgrade head` 가 앱 계정으로 DDL 을 시도해 실패한다."""
        assert "MIGRATION_DATABASE_URL" in listed(locals_["migrate_secret_keys"])

    def test_backend_still_gets_the_runtime_dsn(self, locals_: dict[str, Any]) -> None:
        """전제 선행 단정 — 위 둘이 '시크릿이 아예 없어서' 통과하는 것을 막는다."""
        assert "DATABASE_URL" in listed(locals_["backend_secret_keys"])


class TestRedisUrlKeepsTls:
    """차등 단정 — `data.tf` 의 전송 암호화와 `ecs.tf` 의 스킴을 대조한다.

    두 파일이 따로 바뀌는데, 어긋나면 **연결 자체가 거부된다.** `data.tf` 의
    주석이 "ecs.tf 에서 환경변수를 만들 때 확인한다"고 적어둔 지점이다.
    """

    def test_scheme_is_rediss(self, locals_: dict[str, Any]) -> None:
        url = clean(locals_["common_environment"])["REDIS_URL"]
        assert url.startswith("rediss://"), url

    def test_transit_encryption_is_what_requires_it(
        self, documents: list[dict[str, Any]], locals_: dict[str, Any]
    ) -> None:
        """암호화를 끄면 `rediss://` 를 요구할 근거가 사라진다 — 같이 바꿔야 한다."""
        cache = resources(documents, "aws_elasticache_replication_group")["main"]
        url = clean(locals_["common_environment"])["REDIS_URL"]
        assert cache["transit_encryption_enabled"] is (url.startswith("rediss://"))

    def test_url_points_at_the_primary_endpoint(self, locals_: dict[str, Any]) -> None:
        url = clean(locals_["common_environment"])["REDIS_URL"]
        assert "aws_elasticache_replication_group.main.primary_endpoint_address" in url


class TestProductionGuardsAreSatisfiable:
    """규칙을 테스트가 집행한다 — `assert_production_ready()` 와 같은 조건을 본다.

    그 함수는 **기동을 거부한다**(ADR-019). 거부는 시끄러운 실패라 운영에서
    금방 드러나지만, 그걸 **배포 당일 ECS 로그에서** 처음 보는 것과 커밋 전에
    보는 것은 다르다.
    """

    def test_app_env_is_production(self, environment: dict[str, Any]) -> None:
        assert environment["APP_ENV"] == "production"

    def test_rate_limit_backend_is_redis(self, environment: dict[str, Any]) -> None:
        """ "memory" 는 **프로세스당** 센다. `gunicorn -w 2` 에서 한도가 2배가 된다."""
        assert environment["RATE_LIMIT_BACKEND"] == "redis"

    def test_cors_has_no_localhost(self, environment: dict[str, Any]) -> None:
        assert "localhost" not in environment["CORS_ORIGINS"]

    def test_cors_uses_the_real_domain(self, environment: dict[str, Any]) -> None:
        assert environment["CORS_ORIGINS"] == "https://${var.domain_name}"

    def test_metrics_token_comes_from_the_secret(self, locals_: dict[str, Any]) -> None:
        """평문으로 두면 /metrics 를 지키는 토큰이 콘솔에 보인다."""
        assert "METRICS_TOKEN" in listed(locals_["backend_secret_keys"])

    def test_proxy_headers_are_trusted_behind_the_alb(self, environment: dict[str, Any]) -> None:
        """★ 끄면 속도 제한이 **동작하면서 틀린다.**

        모든 요청이 ALB 의 IP 하나로 보여서 전체 사용자가 한 덩어리로 제한된다.
        부하가 적을 때는 아무 증상이 없다.
        """
        assert environment["TRUST_PROXY_HEADERS"] == "true"


class TestLogsCannotAccumulateForever:
    """규칙을 테스트가 집행한다 + 차등 단정 — 보존 기간과 IAM 을 함께 본다."""

    def test_a_log_group_per_task_definition(self, locals_: dict[str, Any]) -> None:
        assert set(listed(locals_["log_streams"])) == EXPECTED_TASK_DEFINITIONS

    def test_log_group_has_a_retention(self, documents: list[dict[str, Any]]) -> None:
        """★ 빼면 '만료 없음'으로 생기고 저장 비용이 조용히 올라간다."""
        group = resources(documents, LOG_GROUP)["ecs"]
        assert group["retention_in_days"] == "${var.log_retention_days}"

    def test_log_group_name_falls_inside_the_iam_pattern(
        self, documents: list[dict[str, Any]]
    ) -> None:
        """접두사를 바꾸면 로그가 조용히 안 쌓인다 — 태스크는 뜨므로 눈치채기 어렵다."""
        group = resources(documents, LOG_GROUP)["ecs"]
        assert group["name"].startswith(f"/ecs/{NAME_REF}/")

    @pytest.mark.parametrize("key", sorted(EXPECTED_TASK_DEFINITIONS))
    def test_agent_never_creates_the_group_itself(
        self, containers: dict[str, dict[str, Any]], key: str
    ) -> None:
        """에이전트가 만들면 보존 기간 없이 생긴다. 그래서 권한도 빼뒀다."""
        options = dequote_keys(containers[key]["logConfiguration"]["options"])
        assert "awslogs-create-group" not in options
        assert options["awslogs-group"].startswith("${aws_cloudwatch_log_group.ecs[")

    def test_iam_withholds_create_log_group(self, documents: list[dict[str, Any]]) -> None:
        """두 쪽이 같은 전제를 공유한다. 권한을 되살리면 이 테스트가 깬다."""
        policy = data_sources(documents, "aws_iam_policy_document")["ecs_execution"]
        granted = {
            action
            for statement in listed(policy["statement"])
            for action in listed(statement.get("actions"))
        }
        assert "logs:CreateLogGroup" not in granted
        assert "logs:PutLogEvents" in granted


class TestTasksCanActuallyStart:
    """규칙을 테스트가 집행한다 — 퍼블릭 서브넷 결정(ADR-020 결정 1=B)의 귀결.

    공인 IP 가 없으면 **ECR pull·로그 전송·SSM 채널이 전부 막힌다.** 외부 API 를
    부르지 않아도 그렇다 (ADR-020 세부사항 3).
    """

    @pytest.mark.parametrize("key", sorted(EXPECTED_SERVICES))
    def test_public_ip_is_assigned(self, services: dict[str, dict[str, Any]], key: str) -> None:
        network = clean(services[key]["network_configuration"])
        assert network["assign_public_ip"] is True

    @pytest.mark.parametrize("key", sorted(EXPECTED_SERVICES))
    def test_subnets_are_the_public_ones(
        self, services: dict[str, dict[str, Any]], key: str, locals_: dict[str, Any]
    ) -> None:
        network = clean(services[key]["network_configuration"])
        assert network["subnets"] == "${local.task_subnet_ids}"
        expression = locals_["task_subnet_ids"]
        assert "aws_subnet.public" in expression
        assert "aws_subnet.private" not in expression

    @pytest.mark.parametrize("key", sorted(EXPECTED_SERVICES))
    def test_tasks_use_the_tasks_security_group(
        self, services: dict[str, dict[str, Any]], key: str
    ) -> None:
        """ALB 보안그룹을 붙이면 인바운드가 `0.0.0.0/0` 으로 열린다."""
        network = clean(services[key]["network_configuration"])
        assert listed(network["security_groups"]) == ["${aws_security_group.tasks.id}"]

    @pytest.mark.parametrize("key", sorted(EXPECTED_TASK_DEFINITIONS))
    def test_platform_is_arm(self, task_definitions: dict[str, dict[str, Any]], key: str) -> None:
        """★ 이미지가 arm64 로 빌드된다. 어긋나면 `CannotPullContainerError` 인데
        메시지가 아키텍처를 명시하지 않아 원인을 찾기 어렵다."""
        platform = clean(task_definitions[key]["runtime_platform"])
        assert platform["cpu_architecture"] == "ARM64"
        assert platform["operating_system_family"] == "LINUX"

    @pytest.mark.parametrize("key", sorted(EXPECTED_TASK_DEFINITIONS))
    def test_fargate_with_awsvpc(
        self, task_definitions: dict[str, dict[str, Any]], key: str
    ) -> None:
        assert listed(task_definitions[key]["requires_compatibilities"]) == ["FARGATE"]
        assert task_definitions[key]["network_mode"] == "awsvpc"

    @pytest.mark.parametrize("key", sorted(EXPECTED_TASK_DEFINITIONS))
    def test_both_roles_are_set_and_different(
        self, task_definitions: dict[str, dict[str, Any]], key: str
    ) -> None:
        """실행 역할과 태스크 역할을 섞으면 '시크릿을 못 읽는다'가 되는데,
        이름만 보고 반대쪽을 고치게 된다 (iam.tf 의 표 참조)."""
        body = task_definitions[key]
        assert body["execution_role_arn"] == "${aws_iam_role.ecs_execution.arn}"
        assert body["task_role_arn"] == "${aws_iam_role.ecs_task.arn}"


class TestPortsMatchTheTargetGroups:
    """차등 단정 — `alb.tf` 의 타깃그룹 포트와 컨테이너 포트를 대조한다.

    어긋나면 ALB 가 아무 타깃도 정상으로 보지 못해 **영구 503** 이 된다.
    두 파일이 따로 바뀌고, 각 파일만 보면 둘 다 옳아 보인다.
    """

    @pytest.mark.parametrize(("key", "port"), [("backend", 8000), ("frontend", 3000)])
    def test_container_port_equals_target_group_port(
        self,
        containers: dict[str, dict[str, Any]],
        locals_: dict[str, Any],
        key: str,
        port: int,
    ) -> None:
        mapping = listed(containers[key]["portMappings"])
        assert [entry["containerPort"] for entry in mapping] == [port]
        assert clean(locals_["target_groups"])[key]["port"] == port

    @pytest.mark.parametrize(("key", "port"), [("backend", 8000), ("frontend", 3000)])
    def test_service_registers_that_container_and_port(
        self, services: dict[str, dict[str, Any]], key: str, port: int
    ) -> None:
        balancer = clean(services[key]["load_balancer"])
        assert balancer["container_name"] == key
        assert balancer["container_port"] == port
        assert balancer["target_group_arn"] == f'${{aws_lb_target_group.app["{key}"].arn}}'

    def test_migrate_task_opens_no_port(self, containers: dict[str, dict[str, Any]]) -> None:
        """한 번 돌고 끝나는 태스크다. 받을 요청이 없다."""
        assert "portMappings" not in containers["migrate"]


class TestTheDoorToTheDatabaseStaysOpen:
    """규칙을 테스트가 집행한다 — ECS Exec 가 운영 DB 계정을 만드는 **유일한 경로**다.

    RDS 는 프라이빗 서브넷에 있고 보안그룹이 태스크에서 오는 5432 만 받는다.
    NAT 도 배스천도 없다. 이 스위치를 끄면 `roles.sql` 을 돌릴 수단이 사라지고,
    그걸 **장애가 난 뒤에** 알게 된다 (JOURNAL 2026-11-23).
    """

    @pytest.mark.parametrize("key", sorted(EXPECTED_SERVICES))
    def test_execute_command_is_enabled(
        self, services: dict[str, dict[str, Any]], key: str
    ) -> None:
        assert services[key]["enable_execute_command"] is True

    def test_task_role_carries_the_ssm_channel(self, documents: list[dict[str, Any]]) -> None:
        """차등 단정 — 권한과 스위치는 **둘 다** 있어야 열린다. 하나만으로는 안 된다."""
        policy = data_sources(documents, "aws_iam_policy_document")["ecs_task"]
        granted = {
            action
            for statement in listed(policy["statement"])
            for action in listed(statement.get("actions"))
        }
        assert {
            "ssmmessages:CreateControlChannel",
            "ssmmessages:CreateDataChannel",
            "ssmmessages:OpenControlChannel",
            "ssmmessages:OpenDataChannel",
        } <= granted

    def test_init_process_is_enabled_where_we_exec(
        self, containers: dict[str, dict[str, Any]]
    ) -> None:
        """Exec 를 반복하면 defunct 프로세스가 쌓인다. AWS 문서가 권장한다."""
        assert clean(containers["backend"]["linuxParameters"])["initProcessEnabled"] is True


class TestDeploymentsFailLoudly:
    """경계값 분석 + 규칙 집행 — 조용히 재시도하는 배포를 막는다."""

    @pytest.mark.parametrize("key", sorted(EXPECTED_SERVICES))
    def test_circuit_breaker_rolls_back(
        self, services: dict[str, dict[str, Any]], key: str
    ) -> None:
        """없으면 ECS 가 깨진 태스크를 **무한히 재시도**한다. 그 상태가 조용하다."""
        breaker = clean(services[key]["deployment_circuit_breaker"])
        assert breaker["enable"] is True
        assert breaker["rollback"] is True

    @pytest.mark.parametrize("key", sorted(EXPECTED_SERVICES))
    def test_grace_period_is_long_enough_to_boot(
        self, services: dict[str, dict[str, Any]], key: str
    ) -> None:
        """★ 하한을 집는다. 짧게 두면 **뜨는 중인 태스크를 계속 교체**하면서
        영원히 안정되지 않는다 — 로그에는 부팅 로그만 반복된다."""
        grace = services[key]["health_check_grace_period_seconds"]
        assert grace >= 30, grace

    @pytest.mark.parametrize("key", sorted(EXPECTED_SERVICES))
    def test_no_downtime_during_deploy(self, services: dict[str, dict[str, Any]], key: str) -> None:
        """태스크가 1개다. 최소 100% 가 아니면 배포마다 응답이 끊긴다."""
        assert services[key]["deployment_minimum_healthy_percent"] == 100
        assert services[key]["deployment_maximum_percent"] >= 200


class TestImageTagIsPinnedToACommit:
    """규칙을 테스트가 집행한다 — 무엇이 떠 있는지 git 에서 되짚을 수 있어야 한다."""

    def test_no_default_is_provided(self, image_tag: dict[str, Any]) -> None:
        """`latest` 같은 기본값을 두면 **어떤 커밋이 떠 있는지 알 수 없다.**"""
        assert "default" not in image_tag

    def test_a_validation_rejects_anything_but_a_sha(self, image_tag: dict[str, Any]) -> None:
        validation = clean(image_tag["validation"])
        assert "[0-9a-f]{12}" in validation["condition"]

    @pytest.mark.parametrize("key", sorted(EXPECTED_TASK_DEFINITIONS))
    def test_images_use_that_variable(
        self, containers: dict[str, dict[str, Any]], key: str
    ) -> None:
        image = containers[key]["image"]
        assert image.endswith(":${var.image_tag}"), image
        assert "latest" not in image

    def test_images_come_from_our_own_repositories(
        self, containers: dict[str, dict[str, Any]]
    ) -> None:
        """실행 역할은 우리 리포지토리 둘만 pull 할 수 있다 (iam.tf).
        다른 레지스트리를 적으면 권한이 아니라 **pull 이** 막힌다."""
        for key, container in containers.items():
            assert "${aws_ecr_repository.app[" in container["image"], key


class TestTheObservabilitySidecarsAreNotHalfAdded:
    """규칙을 테스트가 집행한다 — 미룬 것이 '반쯤 들어간 상태'로 남지 않게.

    Prometheus·Grafana 는 설정 파일 전달 수단이 없어 이번 범위에서 제외했다
    (Fargate 에 바인드 마운트가 없다). **반쯤 들어가면** 사이드카는 떴는데
    설정이 없어 아무것도 긁지 못하는 상태가 되고, 대시보드는 빈 그래프를
    보여준다 — "지표가 0" 과 "수집이 안 됨" 이 화면에서 같아 보인다.
    """

    def test_no_container_declares_a_bind_mount(
        self, task_definitions: dict[str, dict[str, Any]]
    ) -> None:
        """★ Fargate 는 호스트 바인드 마운트를 제공하지 않는다. `volume` 블록이
        생겼다면 EFS 든 무엇이든 **별도 결정**이 끼어든 것이다."""
        for key, body in task_definitions.items():
            assert "volume" not in body, key

    def test_grafana_credentials_stay_unused_for_now(self, locals_: dict[str, Any]) -> None:
        """시크릿에는 6키가 있지만 지금 주입되는 것은 일부다. Grafana 자격증명이
        주입됐다면 사이드카가 들어왔다는 뜻이고, 그럼 이 파일의 전제가 바뀐다."""
        injected = set(listed(locals_["backend_secret_keys"])) | set(
            listed(locals_["migrate_secret_keys"])
        )
        assert "GF_SECURITY_ADMIN_PASSWORD" not in injected
        assert "DATA_SOURCE_NAME" not in injected


class TestTheCheckIsNotVacuous:
    """전제 선행 단정 — 위 검사들이 '대상이 없어서' 통과하는 것을 막는다.

    파싱이 빗나가면 모든 조회가 빈 값을 돌려주고, 단정은 전부 통과한다.
    그건 '통과'가 아니라 '검증하지 않음'이다 (JOURNAL 2026-11-19).
    """

    def test_services_exist(self, services: dict[str, dict[str, Any]]) -> None:
        assert set(services) == EXPECTED_SERVICES

    def test_containers_parsed_as_objects(self, containers: dict[str, dict[str, Any]]) -> None:
        for key, container in containers.items():
            assert isinstance(container, dict), key
            assert container["name"] == key
            assert container["essential"] is True

    def test_plaintext_environment_is_not_empty(self, locals_: dict[str, Any]) -> None:
        environment = clean(locals_["common_environment"])
        assert isinstance(environment, dict)
        assert len(environment) >= 5, environment

    def test_secret_key_lists_are_not_empty(self, locals_: dict[str, Any]) -> None:
        assert len(listed(locals_["backend_secret_keys"])) >= 2
        assert len(listed(locals_["migrate_secret_keys"])) >= 3

    def test_log_group_resource_exists(self, documents: list[dict[str, Any]]) -> None:
        assert set(resources(documents, LOG_GROUP)) == {"ecs"}
