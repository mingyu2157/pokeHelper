############################################################
# 워크로드 역할 (ADR-021 세부사항 5·6·7)
#
# ★ 이 파일에 없는 것: 운영자 자격증명 ★
#
# IAM 이 두 층으로 갈린다. ① **Terraform 을 돌리는 주체**는 콘솔에서 손으로
# 만든다 — Terraform 이 AWS API 를 호출하려면 먼저 자격증명이 있어야 하는데,
# 그 자격증명을 Terraform 으로 만들 수는 없다. state 버킷을 `bootstrap/` 으로
# 뺀 것과 같은 닭-달걀이다. 절차는 README 1장에 있다.
# ② **워크로드 역할**(아래 셋)만 Terraform 이 만든다.
#
# ★ 자원의 `description` 은 영문이다 — `security_groups.tf` 와 같은 이유로
#   AWS 에 올라가는 문자열은 ASCII 로 둔다. Terraform 의 `output description`
#   은 우리끼리 보는 것이라 한글이다.
############################################################

# ARN 을 문자열로 짜맞출 때 파티션을 박지 않는다. `aws` 말고도
# `aws-cn`·`aws-us-gov` 가 있고, 박아두면 그 계정에서 조용히 안 맞는다.
data "aws_partition" "current" {}

locals {
  # ★ ecs.tf 가 아직 없어서 자원을 참조할 수 없다. 이름 규칙으로 ARN 을 만든다.
  #   그래서 이 파일은 **단독으로 apply 된다** — 역할이 먼저 있어야 태스크 정의가
  #   역할 ARN 을 참조할 수 있으므로 순서가 이쪽이 맞다.
  #
  #   ★ ecs.tf 를 쓸 때 클러스터 이름은 반드시 `local.name` 이어야 한다.
  #     이름이 어긋나면 **정책이 틀렸다는 신호 없이 스케줄러만 조용히 막힌다.**
  ecs_cluster_arn = "arn:${data.aws_partition.current.partition}:ecs:${var.region}:${data.aws_caller_identity.current.account_id}:cluster/${local.name}"

  # 스케줄러가 돌릴 태스크 정의. 수집과 만료 정리 둘이다(ADR-020 결정 4).
  # 패밀리 접두사로 묶고 리비전(`:*`)은 열어둔다 — 배포할 때마다 리비전이
  # 늘어나는데 그때마다 정책을 고칠 수는 없다.
  scheduled_task_definition_arn = "arn:${data.aws_partition.current.partition}:ecs:${var.region}:${data.aws_caller_identity.current.account_id}:task-definition/${local.name}-*:*"

  # RunTask 가 태그를 붙일 대상. 클러스터 이름이 위 규칙을 따른다는 전제다.
  ecs_task_arn_pattern = "arn:${data.aws_partition.current.partition}:ecs:${var.region}:${data.aws_caller_identity.current.account_id}:task/${local.name}/*"
}

############################################################
# 신뢰 정책 — **누가 이 역할을 맡을 수 있는가**
#
# 콘솔 마법사가 숨기는 것이 바로 이 부분이다. 권한 정책(무엇을 할 수 있는가)만
# 보고 신뢰 정책을 안 보면, **엉뚱한 주체가 역할을 맡을 수 있는 상태**를
# 눈치채지 못한다.
############################################################

data "aws_iam_policy_document" "ecs_tasks_trust" {
  statement {
    sid     = "ECSTasksAssume"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }

    # ★ 혼동된 대리인(confused deputy) 방지 ★
    #
    # 서비스 주체는 **모든 계정의 ECS** 를 뜻한다. 조건이 없으면 원리상 남의
    # 계정 ECS 가 우리 역할을 맡으려 시도할 수 있다. 계정을 못 박아 그 경로를
    # 닫는다. 클러스터 ARN(`aws:SourceArn`)까지 묶으면 더 좁지만, 클러스터가
    # 아직 없어 여기서는 계정까지만 한다.
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [data.aws_caller_identity.current.account_id]
    }
  }
}

data "aws_iam_policy_document" "scheduler_trust" {
  statement {
    sid     = "EventBridgeSchedulerAssume"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["scheduler.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [data.aws_caller_identity.current.account_id]
    }
  }
}

############################################################
# 1. 실행 역할 — ECS **에이전트**가 쓴다 (컨테이너 밖)
#
# ★★ 태스크 역할과 혼동하지 않는다 ★★
#
# | | 누가 | 언제 | 무엇을 |
# |---|---|---|---|
# | **실행** 역할 | ECS 에이전트 | 태스크가 **뜨기 전** | ECR pull · 로그 스트림 생성 · 시크릿을 환경변수로 주입 |
# | **태스크** 역할 | 컨테이너 안의 앱 | 도는 동안 | 앱 코드가 호출하는 AWS API |
#
# "시크릿을 못 읽는다"는 거의 항상 **실행 역할** 문제인데, 이름만 보고 태스크
# 역할을 고치게 된다. 컨테이너가 뜨기도 전에 필요한 권한은 전부 이쪽이다.
#
# ★ AWS 관리형 `AmazonECSTaskExecutionRolePolicy` 를 붙이지 않고 직접 쓴다.
#   관리형 정책은 ECR 과 로그를 `Resource: "*"` 로 연다 — 계정의 **모든**
#   리포지토리를 pull 할 수 있고 **모든** 로그 그룹에 쓸 수 있다. 지금은 계정에
#   우리 자원밖에 없어서 차이가 없지만, 그건 "좁다"가 아니라 "넓은데 들어갈
#   곳이 없다"일 뿐이다.
############################################################

resource "aws_iam_role" "ecs_execution" {
  name               = "${local.name}-ecs-execution"
  description        = "ECS agent role: ECR pull, log streams, secret injection (ADR-021)"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_trust.json

  tags = { Name = "${local.name}-ecs-execution" }
}

data "aws_iam_policy_document" "ecs_execution" {
  # ── ECR ────────────────────────────────────────────────
  statement {
    sid = "ECRAuthToken"

    # ★ 이 액션만 `*` 일 수밖에 없다.
    #
    # `GetAuthorizationToken` 은 리포지토리가 아니라 **레지스트리 전체**에
    # 대한 호출이라 자원 수준 권한을 지원하지 않는다. 리포지토리 ARN 을
    # 적으면 pull 이 통째로 막힌다. 그래서 **아래 pull 액션과 분리해서**
    # 범위가 넓은 구간이 눈에 보이게 둔다.
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"]
  }

  statement {
    sid = "ECRPullOurImagesOnly"

    actions = [
      "ecr:BatchCheckLayerAvailability",
      "ecr:GetDownloadUrlForLayer",
      "ecr:BatchGetImage",
    ]

    # 계정의 모든 리포지토리가 아니라 우리 둘만. 문자열이 아니라 자원 참조로
    # 쓴다 — 이름을 바꿔도 같이 따라간다.
    resources = [for repo in aws_ecr_repository.app : repo.arn]
  }

  # ── CloudWatch Logs ────────────────────────────────────
  statement {
    sid = "WriteOurLogsOnly"

    # ★ `logs:CreateLogGroup` 을 **일부러 빼놨다.**
    #
    # 에이전트가 로그 그룹을 직접 만들 수 있으면 보존 기간이 "만료 없음"으로
    # 생긴다. 그러면 로그가 영원히 쌓이고 **비용이 조용히 올라간다.** 로그
    # 그룹은 ecs.tf 에서 Terraform 이 만들어 보존 기간을 강제한다. 따라서
    # awslogs 드라이버의 `awslogs-create-group` 도 켜면 안 된다.
    actions = [
      "logs:CreateLogStream",
      "logs:PutLogEvents",
    ]

    resources = [
      "arn:${data.aws_partition.current.partition}:logs:${var.region}:${data.aws_caller_identity.current.account_id}:log-group:/ecs/${local.name}*:*",
    ]
  }

  # ── 시크릿 (ADR-022) ───────────────────────────────────
  statement {
    sid = "ReadOurAppSecretOnly"

    actions = ["secretsmanager:GetSecretValue"]

    # ★ 시크릿 **하나만.** 특히 `data.tf` 가 만드는 **RDS 마스터 비밀번호
    #   시크릿은 여기에 없다** — 앱은 `champs_app` 으로 붙으므로(ADR-015)
    #   마스터를 읽을 이유가 없다. 넣으면 "앱 컨테이너가 DB 마스터 비밀번호를
    #   읽을 수 있는 상태"가 되는데, 그건 아무 증상도 내지 않는다.
    resources = [aws_secretsmanager_secret.app.arn]
  }

  # ★ `kms:Decrypt` 를 **주지 않는다.**
  #
  # ECS 문서: "Required only if your secret uses a customer managed key and
  # not the default key." 우리는 AWS 관리형 `aws/secretsmanager` 를 쓰고
  # (secrets.tf 가 `kms_key_id` 를 비워둔다), 그 키 정책이 `kms:ViaService` +
  # `kms:CallerAccount` 로 계정 내 호출을 이미 허용한다. 그래서 IAM 쪽에
  # 추가로 적을 것이 없다 — 적으면 **들어갈 곳이 없는 권한**이 하나 는다.
  #
  # ★ 고객 관리형 키로 바꾸면 이 statement 를 되살려야 한다. 그때 증상은
  #   "시크릿을 못 가져온다"로 나오고 메시지는 KMS 를 가리킨다.
}

resource "aws_iam_role_policy" "ecs_execution" {
  name   = "${local.name}-ecs-execution"
  role   = aws_iam_role.ecs_execution.id
  policy = data.aws_iam_policy_document.ecs_execution.json
}

############################################################
# 2. 태스크 역할 — 컨테이너 **안의 앱**이 쓴다
#
# ★ 앱은 AWS API 를 거의 호출하지 않는다. DB 는 PostgreSQL 프로토콜로, 캐시는
#   Redis 프로토콜로, 외부 데이터는 HTTPS 로 간다. 전부 IAM 과 무관하다.
#   그래서 이 역할은 **의도적으로 거의 비어 있다.**
############################################################

resource "aws_iam_role" "ecs_task" {
  name               = "${local.name}-ecs-task"
  description        = "Application role inside the container: ECS Exec only (ADR-021)"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_trust.json

  tags = { Name = "${local.name}-ecs-task" }
}

data "aws_iam_policy_document" "ecs_task" {
  statement {
    sid = "ECSExecSession"

    # ★ Fargate 에는 SSH 가 없다. 도는 컨테이너에 들어가 볼 수단은 ECS Exec
    #   하나뿐이고, 그 채널이 이 네 액션이다.
    #
    #   권한만으로는 열리지 않는다 — 서비스/태스크의 `enable_execute_command`
    #   도 켜야 한다. 권한을 미리 두고 **스위치는 ecs.tf 에서** 잠그는 쪽이,
    #   장애가 난 뒤에 `iam.tf` 를 고치고 apply 하는 것보다 낫다.
    actions = [
      "ssmmessages:CreateControlChannel",
      "ssmmessages:CreateDataChannel",
      "ssmmessages:OpenControlChannel",
      "ssmmessages:OpenDataChannel",
    ]

    # ★ 이 액션들은 자원 수준 권한을 지원하지 않는다. `*` 말고 쓸 수 있는 값이
    #   없다. 좁혀야 할 것은 **누가 세션을 열 수 있는가**이고, 그건 이 역할을
    #   맡을 수 있는 주체(신뢰 정책)와 서비스 쪽 스위치가 정한다.
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "ecs_task" {
  name   = "${local.name}-ecs-task"
  role   = aws_iam_role.ecs_task.id
  policy = data.aws_iam_policy_document.ecs_task.json
}

############################################################
# 3. 스케줄러 역할 — EventBridge Scheduler 가 쓴다
#
# 수집과 만료 정리를 ECS RunTask 로 띄운다 (ADR-020 결정 4).
############################################################

resource "aws_iam_role" "scheduler" {
  name               = "${local.name}-scheduler"
  description        = "EventBridge Scheduler role: ecs:RunTask with narrowed PassRole (ADR-021)"
  assume_role_policy = data.aws_iam_policy_document.scheduler_trust.json

  tags = { Name = "${local.name}-scheduler" }
}

data "aws_iam_policy_document" "scheduler" {
  statement {
    sid       = "RunOurScheduledTasks"
    actions   = ["ecs:RunTask"]
    resources = [local.scheduled_task_definition_arn]

    # 태스크 정의를 우리 것으로 한정해도, 그걸 **다른 클러스터에** 띄우는 것은
    # 아직 막히지 않는다. 클러스터도 묶는다.
    condition {
      test     = "ArnEquals"
      variable = "ecs:cluster"
      values   = [local.ecs_cluster_arn]
    }
  }

  statement {
    sid = "TagTasksItStarts"

    # RunTask 가 태그를 전파하려면 이 권한이 필요하다. 없으면 RunTask 자체가
    # AccessDenied 로 떨어진다.
    actions   = ["ecs:TagResource"]
    resources = [local.ecs_task_arn_pattern]

    # 태스크를 **띄우는 그 호출**에서만. 이미 돌고 있는 자원의 태그를 바꾸는
    # 데는 쓸 수 없다.
    condition {
      test     = "StringEquals"
      variable = "ecs:CreateAction"
      values   = ["RunTask"]
    }
  }

  statement {
    sid = "PassOnlyOurTaskRoles"

    # ★★ 여기가 이 파일에서 가장 위험한 권한이다 (ADR-021 세부사항 7) ★★
    #
    # 태스크를 띄우려면 태스크 정의가 참조하는 역할을 ECS 에게 **넘겨줘야**
    # 하고, 그 자체가 별도 권한이다. 넓게 열면 — `Resource: "*"` 로 두면 —
    # 이 역할을 가진 누구나 **계정의 더 센 역할을 ECS 에 넘겨서** 그 권한으로
    # 컨테이너를 띄울 수 있다. 권한 상승이 한 줄로 완성된다.
    #
    # 그래서 둘을 같이 건다:
    #   ① Resource — 우리 역할 둘만. 문자열이 아니라 자원 참조로.
    #   ② Condition — 넘겨받는 쪽이 ECS 태스크일 때만.
    # 하나만으로는 부족하다. ①만 있으면 그 역할을 ECS 아닌 서비스에 넘길 수
    # 있고, ②만 있으면 계정의 모든 역할을 ECS 에 넘길 수 있다.
    actions = ["iam:PassRole"]

    resources = [
      aws_iam_role.ecs_execution.arn,
      aws_iam_role.ecs_task.arn,
    ]

    condition {
      test     = "StringEquals"
      variable = "iam:PassedToService"
      values   = ["ecs-tasks.amazonaws.com"]
    }
  }
}

resource "aws_iam_role_policy" "scheduler" {
  name   = "${local.name}-scheduler"
  role   = aws_iam_role.scheduler.id
  policy = data.aws_iam_policy_document.scheduler.json
}

############################################################
# 출력 — ecs.tf·scheduler.tf 는 같은 모듈이라 참조로 쓰면 되고, 이 출력은
# 콘솔에서 눈으로 대조할 때를 위한 것이다.
############################################################

output "iam_role_arns" {
  description = "워크로드 역할 셋. 실행/태스크를 섞어 쓰지 않도록 이름으로 구분한다."
  value = {
    ecs_execution = aws_iam_role.ecs_execution.arn
    ecs_task      = aws_iam_role.ecs_task.arn
    scheduler     = aws_iam_role.scheduler.arn
  }
}
