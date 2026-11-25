############################################################
# 수집 스케줄 — EventBridge Scheduler → ECS RunTask
# (ADR-011 / ADR-012 / ADR-020 결정 4)
#
# ★ 이 파일에 "무엇을 돌리는가"가 없다 ★
#
# 돌릴 대상은 `ecs.tf` 의 `aws_ecs_task_definition.ingest` 이고, 실제 일은 그
# 이미지 안의 CLI 가 한다. 이 파일은 **언제 돌릴지만** 안다. ADR-011 이 CLI 와
# 스케줄러를 나눈 선을 그대로 옮긴 것이다 — Airflow DAG 도 같은 모양이었고,
# 그래서 스케줄러를 또 바꿔도 수집은 그대로 돈다.
#
# ★★ Airflow 가 하던 것 중 EventBridge Scheduler 가 **못 하는 것 둘** ★★
#
# ① **작업 간 의존** (`ingest >> cleanup`)
#    → 스케줄을 둘로 나누지 않고 **한 태스크 안에서 `&&`** 로 잇는다.
#      구현은 `ecs.tf` 의 `local.ingest_container.command` 에 있다.
#      스케줄을 둘로 나누면 수집이 실패한 날에도 정리가 돌아서, DAG 주석이
#      경고한 상태("오래된 스냅샷만 지워져 보존 기간이 짧아진다")가 된다.
#
# ② **종료 코드 기반 재시도** (`retries: 2, retry_delay: 10분`)
#    → ★ **포기했다.** 아래 `retry_policy` 주석 참조.
#
# ★ `execution_timeout` 은 컨테이너의 `timeout` 명령으로 옮겼다 (ecs.tf).
#   ECS RunTask 에는 실행 시간 상한이 없다.
############################################################

resource "aws_scheduler_schedule" "daily_ingest" {
  name        = "${local.name}-daily-ingest"
  description = "Collect usage snapshot, then prune expired ones. Runs the backend image once."

  # ★ 기본 그룹을 쓴다. 그룹을 나누는 것은 스케줄이 수십 개가 되어 태그·권한을
  #   그룹 단위로 끊고 싶을 때다. 지금은 하나다.
  group_name = "default"

  # ★ 유연 실행 창을 쓰지 않는다.
  #
  # `mode = "FLEXIBLE"` 이면 AWS 가 지정 시각부터 창 안의 임의 시점에 띄운다.
  # 대규모 팬아웃에서 부하를 흩뜨리는 기능인데, 우리는 하루 한 번이고 **언제
  # 돌았는지가 정확해야** 로그와 스냅샷 시각을 맞춰볼 수 있다.
  flexible_time_window {
    mode = "OFF"
  }

  # ★ 6필드 cron 이고 일·요일 중 하나는 반드시 `?` 다 (EventBridge 규격).
  #   기본값 `cron(0 6 * * ? *)` = 매일 06:00.
  schedule_expression = var.ingest_schedule_expression

  # ★★ 시간대를 **명시한다** ★★
  #
  # 적지 않으면 UTC 다. Airflow DAG 가 바로 그래서 한국 시간 오후 3시에 돌고
  # 있었다 — 주석에는 "매일 06:00" 이라고만 적혀 있어 아무도 몰랐다.
  # 시간대를 적지 않은 스케줄은 **틀렸는지 알 방법이 없다.**
  schedule_expression_timezone = var.ingest_schedule_timezone

  state = "ENABLED"

  target {
    # ECS RunTask 타깃에서 `arn` 은 **클러스터**다. 태스크 정의는 아래
    # `ecs_parameters` 에 들어간다. 헷갈리기 쉬운 자리다.
    arn      = aws_ecs_cluster.main.arn
    role_arn = aws_iam_role.scheduler.arn

    ecs_parameters {
      # ★ 리비전까지 포함된 ARN 을 쓴다 (`...:ingest:3`).
      #
      # `arn_without_revision` 을 쓰면 항상 최신 리비전이 돌아서 편해 보이지만,
      # **무엇이 돌고 있는지가 Terraform 밖에서 바뀐다.** 리비전을 박아두면
      # 태스크 정의를 고쳤을 때 `apply` 를 해야 반영되고, 그게 IaC 다.
      # ECR 태그를 커밋 SHA 로 박은 것과 같은 판단이다.
      task_definition_arn = aws_ecs_task_definition.ingest.arn

      launch_type = "FARGATE"
      task_count  = 1

      network_configuration {
        subnets         = local.task_subnet_ids
        security_groups = [aws_security_group.tasks.id]

        # ★ 퍼블릭 서브넷이므로 필수다. 없으면 ECR pull·CloudWatch Logs 가
        #   전부 막히고, 더 나아가 **수집 대상인 외부 API 에도 못 나간다**
        #   (ADR-020 세부사항 3). 서비스 쪽과 같은 이유다.
        assign_public_ip = true
      }
    }

    # ★★ 재시도하지 않는다 — 그리고 그건 Airflow 와 다르다 ★★
    #
    # DAG 는 `retries: 2 / retry_delay: 10분` 이었다. 그런데 EventBridge 의
    # `retry_policy` 는 **RunTask API 호출이 실패했을 때**만 재시도한다.
    # 태스크가 정상적으로 떠서 종료 코드 1(네트워크)이나 2(매핑 실패)로 죽는
    # 경우는 **재시도 대상이 아니다.** 즉 여기서 숫자를 올려도 DAG 의 동작이
    # 재현되지 않는다 — 재현된다고 착각하는 쪽이 더 나쁘다.
    #
    # 그래서 **0 으로 명시하고 포기한 것을 기록한다.** 하루 1회 작업이고,
    # 수집이 실패해도 이전 스냅샷으로 계속 서비스한다 (CLAUDE.md 8-5).
    # 다음 기회가 24시간 뒤라는 것만 받아들이면 된다.
    #
    # ★ 되돌리는 조건: "하루를 건너뛰는 것"이 실제로 문제가 되면 Step Functions
    #   로 옮긴다. 거기서는 종료 코드 1 만 재시도하고 2 는 알림으로 보낼 수 있다
    #   — DAG 주석이 "지금은 구분하지 않는다"고 적어둔 그 구분이다.
    retry_policy {
      maximum_retry_attempts = 0
    }
  }
}

############################################################
# 출력
############################################################

output "ingest_schedule" {
  description = "수집 스케줄. 시간대가 UTC 가 아니라는 점이 핵심이라 같이 내보낸다."
  value = {
    name       = aws_scheduler_schedule.daily_ingest.name
    expression = aws_scheduler_schedule.daily_ingest.schedule_expression
    timezone   = aws_scheduler_schedule.daily_ingest.schedule_expression_timezone
    state      = aws_scheduler_schedule.daily_ingest.state
  }
}

output "ingest_task_family" {
  description = "수동으로 한 번 돌려볼 때 쓴다 (README 5-F)."
  value       = aws_ecs_task_definition.ingest.family
}
