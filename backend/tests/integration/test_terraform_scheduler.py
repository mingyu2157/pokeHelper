"""수집 스케줄이 **조용히 틀리는 방향**을 막는다 (`infra/terraform/scheduler.tf`).

★ 이 파일이 막는 것 ★

스케줄은 **하루에 한 번만 틀릴 기회가 있고**, 틀려도 아무 경보가 울리지 않는다.
다섯 가지가 특히 그렇다.

- **시간대를 안 적기** — 적지 않으면 UTC 다. Airflow DAG 가 바로 그래서 한국
  시간 **오후 3시**에 돌고 있었고, 주석에는 "매일 06:00" 이라고만 적혀 있어
  아무도 몰랐다. 사용자가 있는 시간에 외부 API 를 50회 부르고 DB 에 쓴다.
- **스케줄을 둘로 쪼개기** — EventBridge 에는 작업 간 의존이 없다. 수집과
  정리를 따로 걸면 **수집이 실패한 날에도 정리가 돈다.** DAG 주석이 경고한
  상태다("오래된 스냅샷만 지워져 보존 기간이 짧아진다"). 둘 다 성공적으로
  보이기까지 한다.
- **`&&` 를 `;` 로 바꾸기** — 한 글자 차이로 의존이 사라진다.
- **`timeout` 빼기** — ECS RunTask 에는 실행 시간 상한이 없다. 멈춘 태스크가
  영원히 돌면서 과금된다. 그걸 알려주는 것은 청구서뿐이다.
- **태스크 정의를 `arn_without_revision` 으로** — 항상 최신이 돌아서 편해
  보이지만 **무엇이 돌고 있는지가 Terraform 밖에서 바뀐다.**

범위와 한계는 `tests/terraform_support.py` 머리말 참조 — **`.tf` 소스를 읽고
실제 AWS 계정은 보지 않는다.** 콘솔에서 비활성화한 스케줄은 잡지 못한다.
"""

from __future__ import annotations

import re
from typing import Any

import pytest

from tests.terraform_support import (
    clean,
    data_sources,
    load_documents,
    local_values,
    resources,
    variables,
)

SCHEDULE = "aws_scheduler_schedule"
TASK_DEFINITION = "aws_ecs_task_definition"

NAME_REF = "${local.name}"

# DAG 가 걸어둔 상한. 이 숫자를 넘기면 "옮긴 것"이 아니라 "바꾼 것"이다.
DAG_INGEST_TIMEOUT_SECONDS = 15 * 60
DAG_CLEANUP_TIMEOUT_SECONDS = 5 * 60


def listed(value: Any) -> list[Any]:
    """hcl2 는 원소가 하나면 리스트를 벗겨서 준다. 항상 리스트로 되돌린다."""
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


@pytest.fixture(scope="module")
def documents() -> list[dict[str, Any]]:
    return load_documents()


@pytest.fixture(scope="module")
def locals_(documents: list[dict[str, Any]]) -> dict[str, Any]:
    return local_values(documents)


@pytest.fixture(scope="module")
def schedules(documents: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return resources(documents, SCHEDULE)


@pytest.fixture(scope="module")
def schedule(schedules: dict[str, dict[str, Any]]) -> dict[str, Any]:
    assert set(schedules) == {"daily_ingest"}, schedules
    return schedules["daily_ingest"]


@pytest.fixture(scope="module")
def target(schedule: dict[str, Any]) -> dict[str, Any]:
    return clean(schedule["target"])


@pytest.fixture(scope="module")
def timeouts(ingest_command: str) -> list[int]:
    """셸 명령에 걸린 `timeout` 초 값들. 순서대로 수집, 정리."""
    return [int(value) for value in re.findall(r"\btimeout (\d+)\b", ingest_command)]


@pytest.fixture(scope="module")
def pair(locals_: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """(migrate, ingest) 컨테이너 정의. 둘이 갈라지지 않았는지 대조한다."""
    return locals_["migrate_container"], locals_["ingest_container"]


@pytest.fixture(scope="module")
def ingest_command(locals_: dict[str, Any]) -> str:
    """수집 컨테이너의 셸 명령 한 줄. 의존성과 타임아웃이 전부 여기 있다."""
    command = listed(locals_["ingest_container"]["command"])
    assert command[:2] == ["sh", "-c"], command
    return command[2]


class TestTheScheduleRunsWhenWeThink:
    """규칙을 테스트가 집행한다 — 시간대를 적지 않은 스케줄은 틀렸는지 알 수 없다."""

    def test_timezone_is_declared(self, schedule: dict[str, Any]) -> None:
        """★ 적지 않으면 UTC 다. 그게 Airflow 에서 실제로 일어난 일이다."""
        assert schedule["schedule_expression_timezone"] == "${var.ingest_schedule_timezone}"

    def test_default_timezone_is_not_utc(self, documents: list[dict[str, Any]]) -> None:
        """★ 기본값까지 본다. 변수로 뺐어도 기본값이 UTC 면 같은 사고가 난다."""
        default = variables(documents)["ingest_schedule_timezone"]["default"]
        assert default not in {"UTC", "Etc/UTC", ""}, default
        assert "/" in default, f"IANA 시간대 이름이어야 한다: {default}"

    def test_cron_has_six_fields_with_a_question_mark(
        self, documents: list[dict[str, Any]]
    ) -> None:
        """★ EventBridge cron 은 6필드고 일·요일 중 하나는 반드시 `?` 다.

        5필드(유닉스 cron)를 넣으면 apply 가 거부하는데, 메시지가 필드 수를
        가리키지 않아 한참 본다.
        """
        default = variables(documents)["ingest_schedule_expression"]["default"]
        match = re.fullmatch(r"cron\((.+)\)", default)
        assert match, default
        fields = match.group(1).split()
        assert len(fields) == 6, fields
        day_of_month, day_of_week = fields[2], fields[4]
        assert "?" in (day_of_month, day_of_week), fields

    def test_flexible_window_is_off(self, schedule: dict[str, Any]) -> None:
        """창을 열면 AWS 가 임의 시점에 띄운다. 로그 시각과 스냅샷 시각을
        맞춰볼 수 없게 된다."""
        assert clean(schedule["flexible_time_window"])["mode"] == "OFF"

    def test_schedule_is_enabled(self, schedule: dict[str, Any]) -> None:
        assert schedule["state"] == "ENABLED"


class TestTheDependencySurvivedTheMoveFromAirflow:
    """★ 규칙을 테스트가 집행한다 — `ingest >> cleanup` 을 잃지 않았는가.

    EventBridge 에는 작업 간 의존이 없다. 한 셸에서 `&&` 로 잇는 것이 유일한
    표현이고, **그 한 글자가 사라져도 둘 다 성공으로 보인다.**
    """

    def test_exactly_one_schedule(self, schedules: dict[str, dict[str, Any]]) -> None:
        """★ 스케줄이 둘이 되는 순간 의존이 사라진다."""
        assert len(schedules) == 1, sorted(schedules)

    def test_both_steps_are_in_one_command(self, ingest_command: str) -> None:
        assert "scripts.run_ingest" in ingest_command
        assert "scripts.cleanup_snapshots" in ingest_command

    def test_cleanup_comes_after_ingest(self, ingest_command: str) -> None:
        assert ingest_command.index("scripts.run_ingest") < ingest_command.index(
            "scripts.cleanup_snapshots"
        )

    def test_they_are_joined_by_and_not_semicolon(self, ingest_command: str) -> None:
        """★ `;` 면 수집이 실패해도 정리가 돈다. 한 글자 차이다."""
        between = ingest_command[
            ingest_command.index("scripts.run_ingest") : ingest_command.index(
                "scripts.cleanup_snapshots"
            )
        ]
        assert "&&" in between, between
        assert ";" not in between, between

    def test_no_separate_cleanup_task_definition(self, documents: list[dict[str, Any]]) -> None:
        """정리를 따로 띄울 수단이 생기면 스케줄을 쪼개고 싶어진다."""
        families = set(resources(documents, TASK_DEFINITION))
        assert "cleanup" not in families, families


class TestRuntimeLimitsReplaceExecutionTimeout:
    """★ 규칙을 테스트가 집행한다 — ECS RunTask 에는 실행 시간 상한이 없다.

    DAG 의 `execution_timeout`(수집 15분 / 정리 5분)을 `timeout` 명령으로
    옮겼다. 빠지면 멈춘 태스크가 영원히 돌고, 알려주는 것은 청구서뿐이다.
    """

    def test_both_steps_are_bounded(self, timeouts: list[int]) -> None:
        assert len(timeouts) == 2, timeouts

    def test_limits_do_not_exceed_what_the_dag_allowed(self, timeouts: list[int]) -> None:
        """★ 상한을 늘리는 것은 '옮긴 것'이 아니라 '바꾼 것'이다."""
        assert timeouts[0] <= DAG_INGEST_TIMEOUT_SECONDS, timeouts
        assert timeouts[1] <= DAG_CLEANUP_TIMEOUT_SECONDS, timeouts

    def test_ingest_limit_fits_inside_its_timeout(
        self, documents: list[dict[str, Any]], timeouts: list[int]
    ) -> None:
        """★ 요청 간 1초 강제(ADR-013)라 `limit` 이 곧 초다.

        limit 을 올리면서 timeout 을 그대로 두면 **매일 124 로 죽는데 그게
        타임아웃인지 수집 실패인지 로그를 봐야 안다.**
        """
        limit = variables(documents)["ingest_limit"]["default"]
        assert limit < timeouts[0], (limit, timeouts[0])


class TestItCanActuallyStart:
    """규칙을 테스트가 집행한다 — 퍼블릭 서브넷 결정(ADR-020 결정 1)의 귀결."""

    def test_target_is_the_cluster_not_the_task(self, target: dict[str, Any]) -> None:
        """★ ECS RunTask 타깃의 `arn` 은 클러스터다. 태스크 정의를 넣으면 안 된다."""
        assert target["arn"] == "${aws_ecs_cluster.main.arn}"

    def test_it_uses_the_scheduler_role(self, target: dict[str, Any]) -> None:
        assert target["role_arn"] == "${aws_iam_role.scheduler.arn}"

    def test_task_definition_is_pinned_to_a_revision(self, target: dict[str, Any]) -> None:
        """★ `arn_without_revision` 이면 **무엇이 돌고 있는지가 Terraform 밖에서
        바뀐다.** ECR 태그를 커밋 SHA 로 박은 것과 같은 판단이다."""
        params = clean(target["ecs_parameters"])
        assert params["task_definition_arn"] == "${aws_ecs_task_definition.ingest.arn}"

    def test_public_ip_is_assigned(self, target: dict[str, Any]) -> None:
        """없으면 ECR pull·로그 전송은 물론 **수집 대상인 외부 API 에도** 못 나간다."""
        network = clean(clean(target["ecs_parameters"])["network_configuration"])
        assert network["assign_public_ip"] is True

    def test_network_matches_the_services(self, target: dict[str, Any]) -> None:
        network = clean(clean(target["ecs_parameters"])["network_configuration"])
        assert network["subnets"] == "${local.task_subnet_ids}"
        assert listed(network["security_groups"]) == ["${aws_security_group.tasks.id}"]

    def test_launch_type_is_fargate(self, target: dict[str, Any]) -> None:
        assert clean(target["ecs_parameters"])["launch_type"] == "FARGATE"


class TestRetryIsExplicitlyZero:
    """★ 규칙을 테스트가 집행한다 — 포기한 것을 **명시**한다.

    DAG 는 10분 간격 2회 재시도였다. EventBridge 의 `retry_policy` 는
    **RunTask API 호출 실패**만 재시도하므로 그 동작이 재현되지 않는다.
    숫자를 올려두면 **재현된다고 착각**하게 되고, 그쪽이 더 나쁘다.
    """

    def test_retry_policy_is_present(self, target: dict[str, Any]) -> None:
        """블록이 없으면 '정했다'와 '안 봤다'를 구분할 수 없다."""
        assert "retry_policy" in target

    def test_retry_attempts_are_zero(self, target: dict[str, Any]) -> None:
        assert clean(target["retry_policy"])["maximum_retry_attempts"] == 0


class TestIamCoversExactlyThisTask:
    """차등 단정 — `iam.tf` 와 `scheduler.tf` 를 대조한다.

    어긋나면 ECS 는 멀쩡하고 **스케줄러만** AccessDenied 로 막히는데, 그건
    매일 06:00 에 한 번씩만 드러난다.
    """

    def test_family_prefix_is_covered_by_the_policy(
        self, documents: list[dict[str, Any]], locals_: dict[str, Any]
    ) -> None:
        family = resources(documents, TASK_DEFINITION)["ingest"]["family"]
        assert family.startswith(f"{NAME_REF}-")
        assert f"task-definition/{NAME_REF}-*:*" in locals_["scheduled_task_definition_arn"]

    def test_policy_pins_the_cluster(
        self, documents: list[dict[str, Any]], locals_: dict[str, Any]
    ) -> None:
        """태스크 정의만 한정하면 **다른 클러스터에 띄우는 것**이 안 막힌다."""
        policy = data_sources(documents, "aws_iam_policy_document")["scheduler"]
        conditions = [
            condition
            for statement in listed(policy["statement"])
            for condition in listed(statement.get("condition"))
        ]
        variables_used = {clean(condition).get("variable") for condition in conditions}
        assert "ecs:cluster" in variables_used, variables_used
        assert f":cluster/{NAME_REF}" in locals_["ecs_cluster_arn"]

    def test_policy_can_pass_the_task_roles(self, documents: list[dict[str, Any]]) -> None:
        """RunTask 는 태스크 정의가 참조하는 역할을 ECS 에 넘겨야 한다."""
        policy = data_sources(documents, "aws_iam_policy_document")["scheduler"]
        granted = {
            action
            for statement in listed(policy["statement"])
            for action in listed(statement.get("actions"))
        }
        assert "ecs:RunTask" in granted
        assert "iam:PassRole" in granted


class TestScheduledIngestMatchesMigrate:
    """★ 차등 단정 — **중복을 없애는 대신 중복이 갈라지는 것을 집행한다.**

    `ingest_container` 와 `migrate_container` 는 거의 같은 내용을 두 번 적는다.
    `merge()` 로 묶지 않은 이유는 python-hcl2 가 함수 호출을 문자열 하나로
    돌려줘서 테스트가 안을 볼 수 없게 되기 때문이다. 그래서 **둘이 어긋나지
    않는지를 여기서 본다.**
    """

    def test_same_image(self, pair: tuple[dict[str, Any], dict[str, Any]]) -> None:
        migrate, ingest = pair
        assert migrate["image"] == ingest["image"]

    def test_same_plaintext_environment(self, pair: tuple[dict[str, Any], dict[str, Any]]) -> None:
        migrate, ingest = pair
        assert migrate["environment"] == ingest["environment"]

    def test_both_use_the_same_secret_arn_form(
        self, pair: tuple[dict[str, Any], dict[str, Any]]
    ) -> None:
        """`:<키>::` 접미사가 한쪽에서만 빠지면 그쪽만 JSON 통째로 받는다."""
        for container in pair:
            assert "::" in container["secrets"]

    def test_ingest_does_not_get_the_ddl_credential(self, locals_: dict[str, Any]) -> None:
        """★ 여기는 **달라야 한다.** 매일 무인으로 도는 태스크에 DDL 자격증명을
        들려 보낼 이유가 없다 (ADR-015)."""
        assert "MIGRATION_DATABASE_URL" not in listed(locals_["ingest_secret_keys"])
        assert "MIGRATION_DATABASE_URL" in listed(locals_["migrate_secret_keys"])

    def test_ingest_still_gets_the_runtime_dsn(self, locals_: dict[str, Any]) -> None:
        """전제 선행 단정 — 위가 '시크릿이 아예 없어서' 통과하는 것을 막는다."""
        assert "DATABASE_URL" in listed(locals_["ingest_secret_keys"])

    def test_neither_opens_a_port(self, pair: tuple[dict[str, Any], dict[str, Any]]) -> None:
        for container in pair:
            assert "portMappings" not in container


class TestLogsAreSeparate:
    """규칙을 테스트가 집행한다 — "어젯밤 수집이 돌았나"를 볼 수 있어야 한다.

    `migrate` 는 배포할 때 사람이 돌리는 것이고 수집은 매일 무인으로 돈다.
    로그 그룹이 같으면 둘이 한 스트림에 섞이고, 나중에 수집 실패 경보를 붙일
    대상도 사라진다.
    """

    def test_ingest_has_its_own_log_group(self, locals_: dict[str, Any]) -> None:
        streams = listed(locals_["log_streams"])
        assert "ingest" in streams
        assert "migrate" in streams

    def test_the_two_containers_write_to_different_groups(self, locals_: dict[str, Any]) -> None:
        def group(container: dict[str, Any]) -> str:
            options = clean(container["logConfiguration"]["options"])
            return next(value for key, value in options.items() if "awslogs-group" in key)

        assert group(locals_["migrate_container"]) != group(locals_["ingest_container"])

    def test_ingest_group_is_declared_by_terraform(self, locals_: dict[str, Any]) -> None:
        """에이전트가 만들면 보존 기간 없이 생긴다 (iam.tf 가 권한을 뺐다)."""
        options = clean(locals_["ingest_container"]["logConfiguration"]["options"])
        assert not any("awslogs-create-group" in key for key in options)
        group = next(value for key, value in options.items() if "awslogs-group" in key)
        assert group.startswith("${aws_cloudwatch_log_group.ecs[")


class TestDefaultsSatisfyTheirOwnValidation:
    """★ 규칙을 테스트가 집행한다 — **기본값이 자기 validation 에 걸리지 않는가.**

    `contains([...], var.x)` 로 허용 목록을 적어놓고 **기본값을 그 밖의 값으로**
    두면, 변수를 넘기지 않은 사람은 plan 단계에서야 막힌다. 더 나쁜 경우는
    오타가 허용 목록 **안에 있는 다른 값**일 때로, 그러면 아무도 못 잡는다.

    이 검사는 돌연변이 검증에서 **실제로 뚫린 구멍**을 메운 것이다
    (`ingest_format` 기본값을 `"Single"` 로 바꿔도 전부 통과했다, 2026-11-25).
    """

    @staticmethod
    def _listed_values(condition: str) -> tuple[list[str], bool]:
        """`contains(["A", "B"], var.x)` 의 목록과 **부정 여부**를 돌려준다.

        ★ `contains` 는 두 방향으로 쓰인다 — 허용 목록(`contains(...)`)과
        금지 목록(`!contains(...)`). 한쪽만 가정하면 다른 쪽에서 거짓 양성이
        난다. 실제로 `db_master_username` 이 금지 목록이라 그렇게 됐다.
        """
        match = re.search(r"(!\s*)?contains\(\s*\[(.*?)\]", condition, re.S)
        if not match:
            return [], False
        return re.findall(r'"([^"]*)"', match.group(2)), bool(match.group(1))

    def test_every_contains_validation_accepts_its_default(
        self, documents: list[dict[str, Any]]
    ) -> None:
        checked: list[str] = []
        for name, body in variables(documents).items():
            if "default" not in body or "validation" not in body:
                continue
            for rule in listed(body["validation"]):
                condition = clean(rule).get("condition", "")
                if not isinstance(condition, str) or "contains(" not in condition:
                    continue
                values, negated = self._listed_values(condition)
                if not values:  # 숫자 목록 등 문자열이 아닌 경우
                    continue
                default = body["default"]
                if isinstance(default, str) and "lower(" in condition:
                    default = default.lower()
                if negated:
                    assert default not in values, (name, "금지 목록에 있다", default, values)
                else:
                    assert default in values, (name, "허용 목록에 없다", default, values)
                checked.append(name)

        # 전제 선행 단정 — 검사 대상이 0건이면 이 테스트는 아무것도 안 한 것이다.
        assert len(checked) >= 2, f"contains() validation 을 가진 변수가 너무 적다: {checked}"

    def test_ingest_format_is_one_the_api_accepts(self, documents: list[dict[str, Any]]) -> None:
        """위 일반 검사가 `ingest_format` 을 실제로 덮는지 못 박는다."""
        body = variables(documents)["ingest_format"]
        condition = clean(listed(body["validation"])[0])["condition"]
        values, negated = self._listed_values(condition)
        assert not negated, condition
        assert set(values) == {"Singles", "Doubles"}
        assert body["default"] == "Singles"


class TestTheCheckIsNotVacuous:
    """전제 선행 단정 — 위 검사들이 '대상이 없어서' 통과하는 것을 막는다."""

    def test_schedule_parsed_as_an_object(self, schedule: dict[str, Any]) -> None:
        assert isinstance(schedule, dict)
        assert schedule["name"] == f"{NAME_REF}-daily-ingest"

    def test_target_parsed_as_an_object(self, target: dict[str, Any]) -> None:
        assert isinstance(target, dict)
        assert "ecs_parameters" in target

    def test_command_is_a_real_shell_line(self, ingest_command: str) -> None:
        assert len(ingest_command) > 60, ingest_command

    def test_ingest_task_definition_exists(self, documents: list[dict[str, Any]]) -> None:
        assert "ingest" in resources(documents, TASK_DEFINITION)

    def test_schedule_variables_exist(self, documents: list[dict[str, Any]]) -> None:
        declared = variables(documents)
        for name in ("ingest_format", "ingest_limit", "ingest_schedule_expression"):
            assert name in declared, sorted(declared)
