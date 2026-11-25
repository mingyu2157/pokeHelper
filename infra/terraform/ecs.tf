############################################################
# ECS — 클러스터 · 태스크 정의 · 서비스 (ADR-020 결정 1·6·7)
#
# ★★ 클러스터 이름은 `local.name` 이어야 한다 ★★
#
# `iam.tf` 의 스케줄러 정책이 클러스터 ARN 을 **이름 규칙으로 짜맞춘다**
# (`local.ecs_cluster_arn`). ecs.tf 가 없던 시점에 정책을 먼저 썼기 때문이다.
# 이름이 어긋나면 **정책이 틀렸다는 신호 없이 스케줄러만 조용히 막힌다** —
# RunTask 가 AccessDenied 로 떨어지는데, 메시지는 클러스터 이름을 가리키지 않는다.
# `tests/integration/test_terraform_ecs.py` 가 두 파일을 교차검증한다.
#
# ★ 태스크 정의 패밀리는 `${local.name}-` 로 시작해야 한다 ★
# 같은 이유다 — `local.scheduled_task_definition_arn` 이
# `task-definition/pokehelper-prod-*:*` 로 한정한다.
#
# ★ 관측 사이드카(Prometheus·Grafana·postgres-exporter)는 이 파일에 없다 ★
#
# ADR-020 결정 5 는 사이드카로 띄우기로 했고 "`prometheus.yml` 을 거의 그대로
# 쓴다"고 적었다. 구현에 들어가 보니 **전달 수단이 없다** — 로컬은 설정 파일
# 6개를 바인드 마운트로 받는데(`docker-compose.yml` 의 prometheus·grafana),
# **Fargate 에는 바인드 마운트가 없다.** 설정을 담은 이미지를 굽거나 EFS 를
# 붙이거나 해야 하고, 그건 별도 결정이다.
#
# 그래서 이번 커밋은 `/health` 200 까지만 간다. 사이드카는 다음 커밋이다.
# ALB 의 `/grafana` 타깃그룹은 **비어 있어서 503 을 돌려준다** — 숨기지 않고
# 그대로 둔다 (`alb.tf` 의 grafana 규칙 주석 참조).
#
# ★ 그리고 cadvisor 는 Fargate 에서 **불가능하다.** 선택이 아니라 제약이다 —
#   `privileged: true` 와 `/var/lib/docker`·`/sys`·`/` 바인드 마운트를
#   요구하는데 Fargate 는 넷 다 제공하지 않는다. 운영 `prometheus.yml` 에서
#   `cadvisor` job 은 빠져야 한다. 컨테이너 단위 수치가 필요하면 ECS 태스크
#   메타데이터 엔드포인트 기반으로 다시 설계해야 한다.
############################################################

resource "aws_ecs_cluster" "main" {
  name = local.name # ★ 위 주석 — iam.tf 가 이 이름을 가정한다

  # ★ Container Insights 를 끈다. 켜면 CloudWatch 사용자 지정 지표로 과금되고,
  #   우리는 지표를 Prometheus 로 보기로 했다 (ADR-020 세부사항 7).
  #   **명시적으로 끈다** — 계정 수준 기본값이 켜져 있을 수 있어서, 적지 않으면
  #   "우리가 끈 것"과 "기본값이 꺼져 있던 것"을 구분할 수 없다.
  setting {
    name  = "containerInsights"
    value = "disabled"
  }

  tags = { Name = local.name }
}

############################################################
# 로그 그룹 — ★ Terraform 이 만든다 ★
#
# `iam.tf` 가 실행 역할에서 `logs:CreateLogGroup` 을 **일부러 뺐다.** 에이전트가
# 그룹을 직접 만들면 보존 기간이 "만료 없음"으로 생기고 로그가 영원히 쌓인다.
# 그룹을 여기서 만들어 보존 기간을 강제한다.
#
# ★ 따라서 `awslogs-create-group` 을 켜면 안 된다. 켜면 권한이 없어 태스크가
#   아예 뜨지 않는다(실패하는 쪽이라 다행이다). 테스트가 부재를 단정한다.
#
# 이름이 `/ecs/${local.name}/…` 이라 iam.tf 의 자원 패턴
# (`log-group:/ecs/pokehelper-prod*:*`)에 걸린다. 접두사를 바꾸면 로그가
# 조용히 안 쌓인다 — 태스크는 뜨므로 눈치채기 어렵다.
############################################################

locals {
  # ★ 평범한 리스트로 둔다. `toset(...)` 을 여기 쓰면 python-hcl2 가 호출식
  #   전체를 문자열 하나로 돌려줘서 테스트가 원소를 볼 수 없다 (컨테이너 정의를
  #   locals 로 뺀 것과 같은 이유). 집합 변환은 쓰는 쪽에서 한다.
  log_streams = ["backend", "frontend", "migrate", "ingest"]
}

resource "aws_cloudwatch_log_group" "ecs" {
  for_each = toset(local.log_streams)

  name              = "/ecs/${local.name}/${each.key}"
  retention_in_days = var.log_retention_days

  tags = { Name = "/ecs/${local.name}/${each.key}" }
}

############################################################
# 환경변수와 시크릿
#
# ★ 시크릿 키 이름 = 환경변수 이름이라 매핑이 기계적이다 (secrets.tf 의 의도).
#   `valueFrom` 끝의 `:<키>::` 가 JSON 에서 그 키 하나만 꺼낸다. 시크릿 하나로
#   6키를 담아 월 $0.40 에 맞춘 구성이다 (ADR-022).
############################################################

locals {
  app_secret_arn = aws_secretsmanager_secret.app.arn

  # ── 앱 컨테이너가 받는 시크릿 ──────────────────────────
  #
  # ★ `MIGRATION_DATABASE_URL` 이 **없다.** 의도다.
  #
  # `app/core/config.py` 가 직접 적어뒀다: "런타임 코드가 이 값을 읽으면
  # 분리의 의미가 없어진다 — alembic/env.py 만 읽는다". 그 계정이 DDL 을 가진
  # 유일한 계정이고(ADR-015), 앱 컨테이너에 주입하면 **앱 프로세스가 DDL 자격
  # 증명을 들고 도는 상태**가 된다. 그 상태는 아무 증상도 내지 않는다.
  #
  # ★ `docker-compose.prod.yml` 은 backend 서비스에 둘 다 준다. 로컬 검증의
  #   편의였고, 운영에서는 따라가지 않는다. 두 파일이 다른 이유를 여기 적어둔다.
  backend_secret_keys = ["DATABASE_URL", "METRICS_TOKEN"]

  # ── 마이그레이션 태스크가 받는 시크릿 ──────────────────
  #
  # 이쪽은 둘 다 필요하다. `alembic upgrade head` 는 MIGRATION_, 시드 적재
  # (`scripts/load_seed_to_db.py` → `app.db.session.get_engine`)는 DATABASE_ 를
  # 읽는다. 같은 태스크 정의로 둘을 돌린다 — 아래 패밀리 주석 참조.
  migrate_secret_keys = ["DATABASE_URL", "MIGRATION_DATABASE_URL", "METRICS_TOKEN"]

  # ── 수집 태스크가 받는 시크릿 ─────────────────────────
  #
  # ★ `MIGRATION_DATABASE_URL` 이 **없다.** 수집은 DDL 을 쓰지 않는다 —
  #   `scripts/run_ingest.py` 와 `scripts/cleanup_snapshots.py` 는 둘 다
  #   `app.db.session.get_engine` 을 거쳐 `DATABASE_URL`(= `champs_app`)로 붙고,
  #   연산이 INSERT·UPDATE·DELETE 뿐이다. TRUNCATE 도 쓰지 않는다
  #   (`roles.sql` 4장이 일부러 주지 않았다).
  #   매일 무인으로 도는 태스크에 DDL 자격증명을 들려 보낼 이유가 없다.
  ingest_secret_keys = ["DATABASE_URL", "METRICS_TOKEN"]

  # ── 평문 환경변수 ─────────────────────────────────────
  #
  # ★ 여기 있는 것은 **비밀이 아닌 것만**이다. 비밀번호가 섞인 DSN 은 전부
  #   위 시크릿으로 간다. 평문 환경변수는 `describe-task-definition` 으로
  #   누구나 읽을 수 있다 — 콘솔에도 그대로 보인다.
  common_environment = {
    APP_ENV = "production"

    # ★ 아래 셋은 `assert_production_ready()` 가 검사한다 (ADR-019).
    #   하나라도 어긋나면 **기동을 거부한다.** 그게 의도다.
    #
    #   - RATE_LIMIT_BACKEND: "memory" 는 프로세스당 세므로 `gunicorn -w 2` 에서
    #     실효 한도가 2배가 된다. 운영은 redis 여야 한다.
    #   - CORS_ORIGINS: "localhost" 가 남아 있으면 거부된다.
    #   - METRICS_TOKEN: 비어 있으면 거부된다 → 시크릿으로 주입한다.
    RATE_LIMIT_BACKEND = "redis"
    CORS_ORIGINS       = "https://${var.domain_name}"

    # ★ ALB 뒤에 있으므로 켠다. 로컬 검증(`docker-compose.prod.yml`)에서는
    #   프록시가 없어 끄는데, 프록시 없이 켜면 X-Forwarded-For 를 그대로 믿어
    #   속도 제한이 통째로 우회된다. 여기서는 ALB 가 그 헤더를 쓰므로 켜는 쪽이
    #   맞다 — 켜지 않으면 모든 요청이 ALB 의 IP 하나로 보여 **전체가 한 덩어리로
    #   제한된다.**
    TRUST_PROXY_HEADERS = "true"

    # ★★ `rediss://` — s 가 둘이다 ★★
    #
    # `data.tf` 가 `transit_encryption_enabled = true` 로 뒀다. 전송 암호화를
    # 켜면 클라이언트가 **TLS 로 붙어야** 하고, `redis://` 로 주면 연결이
    # 거부된다. redis-py 의 `Redis.from_url` 이 스킴으로 TLS 를 켠다
    # (`app/api/deps.py`). data.tf 의 주석이 "ecs.tf 에서 확인한다"고 적어둔
    # 바로 그 지점이다.
    #
    # 엔드포인트 주소는 비밀이 아니다 — 프라이빗 서브넷에 있고 보안그룹이
    # 태스크에서 오는 6379 만 받는다. 그래서 시크릿이 아니라 평문이다.
    REDIS_URL = "rediss://${aws_elasticache_replication_group.main.primary_endpoint_address}:6379/0"
  }
}

############################################################
# 컨테이너 정의 — ★ `locals` 에 둔다. 이유가 하나뿐이다 ★
#
# `container_definitions = jsonencode({...})` 를 리소스 안에 직접 쓰면
# python-hcl2 가 그 전체를 **문자열 하나**로 돌려준다. 그러면 테스트가
# "시크릿이 평문 environment 에 섞였는가" 같은 것을 **볼 수 없다.**
#
# `iam.tf` 가 IAM 정책을 `jsonencode` 대신 `aws_iam_policy_document` data
# 소스로 쓴 것과 같은 판단이다 (`tests/terraform_support.py` 의
# `data_sources` 독스트링에 그 이유가 적혀 있다). **테스트가 읽을 수 있는
# 형태로 쓰는 것**이 조건이다.
############################################################

locals {
  backend_container = {
    name      = "backend"
    image     = "${aws_ecr_repository.app["backend"].repository_url}:${var.image_tag}"
    essential = true

    portMappings = [{ containerPort = 8000, protocol = "tcp" }]

    environment = [for key, value in local.common_environment : { name = key, value = value }]
    secrets = [
      for key in local.backend_secret_keys :
      { name = key, valueFrom = "${local.app_secret_arn}:${key}::" }
    ]

    # ★ ECS Exec 로 들어갈 때 좀비 프로세스가 남지 않게 한다. AWS 문서가
    #   Exec 를 쓸 때 권장한다. 켜지 않으면 세션을 반복할수록 defunct 가 쌓인다.
    linuxParameters = { initProcessEnabled = true }

    logConfiguration = {
      logDriver = "awslogs"
      options = {
        # ★ `awslogs-create-group` 이 **없다.** 위 로그 그룹 주석 참조.
        "awslogs-group"         = aws_cloudwatch_log_group.ecs["backend"].name
        "awslogs-region"        = var.region
        "awslogs-stream-prefix" = "backend"
      }
    }
  }

  frontend_container = {
    name      = "frontend"
    image     = "${aws_ecr_repository.app["frontend"].repository_url}:${var.image_tag}"
    essential = true

    portMappings = [{ containerPort = 3000, protocol = "tcp" }]

    # ★ 시크릿이 없다. 프론트는 DB·Redis·토큰을 모른다 — 전부 백엔드를 거친다.
    environment = [
      { name = "NODE_ENV", value = "production" },
      { name = "SERVER_API_URL", value = "https://${var.domain_name}" },
    ]

    linuxParameters = { initProcessEnabled = true }

    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = aws_cloudwatch_log_group.ecs["frontend"].name
        "awslogs-region"        = var.region
        "awslogs-stream-prefix" = "frontend"
      }
    }
  }

  migrate_container = {
    name      = "migrate"
    image     = "${aws_ecr_repository.app["backend"].repository_url}:${var.image_tag}"
    essential = true

    # 기본은 스키마 적용. 시드는 `--overrides` 로 바꾼다 (아래 패밀리 주석).
    command = ["alembic", "upgrade", "head"]

    # ★ portMappings 가 없다. 받을 요청이 없다.

    environment = [for key, value in local.common_environment : { name = key, value = value }]
    secrets = [
      for key in local.migrate_secret_keys :
      { name = key, valueFrom = "${local.app_secret_arn}:${key}::" }
    ]

    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = aws_cloudwatch_log_group.ecs["migrate"].name
        "awslogs-region"        = var.region
        "awslogs-stream-prefix" = "migrate"
      }
    }
  }

  ############################################################
  # 수집 컨테이너 — ★ 스케줄러가 매일 돌린다 (scheduler.tf)
  #
  # ★★ 왜 `migrate` 를 재사용하지 않고 따로 두는가 ★★
  #
  # 이미지·역할·시크릿이 같아서 `--overrides` 로 명령만 바꿔도 돈다. 실제로
  # 2026-11-25 의 수동 검증은 그렇게 했다. 그런데 **상시 스케줄로는 분리한다.**
  #
  #   1. ★ **로그가 섞이면 "어젯밤 수집이 돌았나"를 못 본다.** `migrate` 는
  #      배포할 때 사람이 돌리는 것이고 수집은 매일 무인으로 도는 것이다.
  #      로그 그룹이 같으면 둘이 한 스트림에 섞인다.
  #   2. **기본 command 가 진짜 명령이어야 한다.** 스케줄에 `--overrides` 를
  #      넣으면 "이 태스크가 무엇을 하는가"가 두 파일에 나뉜다.
  #   3. 나중에 수집 실패 경보를 붙일 때 **대상 로그 그룹이 분리돼 있어야** 한다.
  #
  # ★ 대신 두 컨테이너 정의가 **거의 같은 내용을 두 번** 적는다. `merge()` 로
  #   묶지 않은 이유는 python-hcl2 가 함수 호출을 문자열 하나로 돌려줘서
  #   **테스트가 안을 볼 수 없게 되기 때문**이다(이 파일 위쪽 주석과 같은 이유).
  #   대신 **둘이 어긋나지 않는지를 테스트가 교차검증한다**
  #   (`TestScheduledIngestMatchesMigrate`). 중복을 없애는 대신 중복이 갈라지는
  #   것을 집행한다.
  ############################################################

  ingest_container = {
    name      = "ingest"
    image     = "${aws_ecr_repository.app["backend"].repository_url}:${var.image_tag}"
    essential = true

    # ★★ `&&` 가 Airflow DAG 의 `ingest >> cleanup` 을 대신한다 ★★
    #
    # EventBridge Scheduler 에는 **작업 간 의존 개념이 없다.** 스케줄을 둘로
    # 나누면 수집이 실패한 날에도 정리가 돌아서, DAG 주석이 경고한 상태
    # ("수집이 실패한 날 오래된 스냅샷만 지워져 보존 기간이 짧아진다")가 된다.
    # 한 셸에서 `&&` 로 이으면 **수집이 0 으로 끝날 때만** 정리가 돈다.
    #
    # ★ 종료 코드 2(적재는 됐지만 한영 매핑 해석 실패)도 정리를 막는다.
    #   그것도 DAG 와 같다 — Airflow 에서 2 는 실패이고 하류가 돌지 않았다.
    #
    # ★★ `timeout` 이 DAG 의 `execution_timeout` 을 대신한다 ★★
    #
    # ECS RunTask 에는 **실행 시간 상한이 없다.** 멈춘 태스크가 영원히 돌면서
    # 과금된다. DAG 는 수집 15분 / 정리 5분을 걸어뒀고, 그 숫자를 그대로 옮긴다.
    # `timeout` 은 상한을 넘기면 124 로 끝내므로 `&&` 가 거기서 멈춘다.
    command = [
      "sh", "-c",
      "timeout 900 python -m scripts.run_ingest --format ${var.ingest_format} --limit ${var.ingest_limit} && timeout 300 python -m scripts.cleanup_snapshots",
    ]

    # ★ 수집은 `champs_app` 으로 쓴다 (`DATABASE_URL`). DDL 을 쓰지 않으므로
    #   마이그레이션 계정이 필요 없다 — `migrate` 보다 **한 키 적다**.
    environment = [for key, value in local.common_environment : { name = key, value = value }]
    secrets = [
      for key in local.ingest_secret_keys :
      { name = key, valueFrom = "${local.app_secret_arn}:${key}::" }
    ]

    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = aws_cloudwatch_log_group.ecs["ingest"].name
        "awslogs-region"        = var.region
        "awslogs-stream-prefix" = "ingest"
      }
    }
  }
}

############################################################
# 백엔드 태스크 정의
############################################################

resource "aws_ecs_task_definition" "backend" {
  family = "${local.name}-backend" # ★ 접두사 — iam.tf 참조

  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"

  cpu    = var.backend_cpu
  memory = var.backend_memory

  # ★ ARM(Graviton) — x86 대비 약 20% 싸다 (ADR-020 세부사항 6).
  #   이미지도 `linux/arm64` 로 빌드돼 있어야 한다
  #   (`infra/scripts/push-images.ps1` 이 buildx 로 만든다).
  #   어긋나면 태스크가 `CannotPullContainerError` 로 떨어지는데, 메시지가
  #   아키텍처를 명시하지 않아 원인을 찾기 어렵다.
  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "ARM64"
  }

  # 실행 역할 = ECS 에이전트(컨테이너 **밖**). ECR pull · 로그 · 시크릿 주입.
  execution_role_arn = aws_iam_role.ecs_execution.arn
  # 태스크 역할 = 컨테이너 **안**의 앱. 지금은 ECS Exec 채널뿐이다.
  task_role_arn = aws_iam_role.ecs_task.arn

  container_definitions = jsonencode([local.backend_container])

  tags = { Name = "${local.name}-backend" }
}

############################################################
# 프론트엔드 태스크 정의
#
# ★ SSR 이 백엔드를 **ALB 를 거쳐** 부른다 (2026-11-24 결정) ★
#
# 로컬 compose 는 `SERVER_API_URL: http://backend:8000` 으로 컨테이너 이름을
# 쓴다. Fargate 에는 그 이름이 없다 — ADR-020 은 프론트를 별도 태스크로
# 정했지만 **둘이 서로를 찾는 방법은 정하지 않았다.**
#
# 고른 것: 공인 ALB 주소를 그대로 쓴다. 코드 변경 0, 새 구성요소 0 이고
# ADR-020 구성도가 그대로 유지된다. 대신 SSR 요청이 공인 ALB 를 한 번
# 왕복한다(헤어핀) — 같은 리전이라 수 ms 이고 ALB LCU 가 조금 늘어난다.
#
# 되돌리는 조건: SSR 지연이 **측정 가능하게** 문제가 되거나 ALB LCU 비용이
# 눈에 보이게 늘면 ECS Service Connect 로 바꾼다. 그쪽은 태스크마다 Envoy
# 프록시(~64MB)가 붙는 대신 왕복이 VPC 안에서 끝난다.
#
# ★★ `NEXT_PUBLIC_API_URL` 이 **없다** — 넣어도 동작하지 않기 때문이다 ★★
#
# Next.js 는 `NEXT_PUBLIC_*` 를 **빌드 시점에 번들에 인라인한다.** 런타임
# 환경변수로 주면 브라우저 코드는 그 값을 보지 못한다. 그리고
# `frontend/Dockerfile` 의 builder 스테이지는 그 변수를 받지 않으므로, 지금
# 이미지의 브라우저 번들에는 `http://localhost:8000` 이 박혀 있다
# (`frontend/src/lib/api/config.ts` 의 기본값).
#
# ★ 그래서 **클라이언트 컴포넌트 하나가 운영에서 깨진다** —
#   `components/health-status.tsx` 가 브라우저에서 `/health` 를 직접 부른다.
#   SSR 로 렌더되는 페이지는 정상이고(서버 쪽은 런타임에 읽는다), 그 위젯만
#   실패한다. 고치려면 Dockerfile 에 build arg 를 추가해 다시 빌드해야 하고,
#   그러면 **이미지가 도메인에 묶인다.** 트레이드오프가 있어 별건으로 둔다
#   (JOURNAL 2026-11-24). 여기에 쓸모없는 환경변수를 넣어 "설정했다"고
#   착각하게 만들지 않는다.
############################################################

resource "aws_ecs_task_definition" "frontend" {
  family = "${local.name}-frontend"

  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"

  cpu    = var.frontend_cpu
  memory = var.frontend_memory

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "ARM64"
  }

  execution_role_arn = aws_iam_role.ecs_execution.arn
  task_role_arn      = aws_iam_role.ecs_task.arn

  container_definitions = jsonencode([local.frontend_container])

  tags = { Name = "${local.name}-frontend" }
}

############################################################
# 마이그레이션 · 시드 태스크 정의 — ★ 서비스가 아니다 ★
#
# 한 번 돌고 끝난다. `aws ecs run-task` 로 띄우고, 끝나면 사라진다.
# `docker-compose.prod.yml` 의 `migrate` 서비스에 대응한다 — 거기서 이미
# 운영 이미지로 검증해둔 구성이다.
#
# ★ 같은 태스크 정의로 두 가지를 돌린다. `run-task --overrides` 가 command 를
#   바꾼다:
#     스키마:  alembic upgrade head          (기본 command)
#     시드:    python -m scripts.load_seed_to_db
#
#   패밀리를 둘로 나누지 않은 이유: 이미지·역할·로그·시크릿이 전부 같고,
#   다른 것은 명령 한 줄뿐이다. 나누면 **같은 것을 두 군데서 고쳐야 하고**,
#   한쪽만 고쳐질 때 조용히 갈라진다 (Dockerfile 의 dbclient 스테이지를
#   공유한 것과 같은 판단).
#
# ★ `roles.sql` 은 이 태스크로 돌리지 않는다. DB 계정이 **없는 상태**에서
#   돌려야 하는데, 이 태스크는 그 계정으로 붙는다. 순서가 반대다.
#   계정 생성은 백엔드 태스크에 ECS Exec 로 들어가 `champs_master` 로
#   `psql -f /app/sql/roles.sql` 을 돌린다 (절차는 README 5-D — ★ 실제로
#   돌려본 뒤에 쓴다).
############################################################

resource "aws_ecs_task_definition" "migrate" {
  family = "${local.name}-migrate"

  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"

  # ★ 서비스보다 작게 둔다. 한 번 돌고 끝나므로 상시 비용이 아니고, alembic 은
  #   CPU 를 쓰지 않는다. Fargate 가 받는 조합이어야 한다 — 256 vCPU 는
  #   512/1024/2048 MiB 만 된다.
  cpu    = 256
  memory = 512

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "ARM64"
  }

  execution_role_arn = aws_iam_role.ecs_execution.arn
  task_role_arn      = aws_iam_role.ecs_task.arn

  container_definitions = jsonencode([local.migrate_container])

  tags = { Name = "${local.name}-migrate" }
}

############################################################
# 수집 태스크 정의 — ★ 스케줄러가 매일 돌린다
#
# 패밀리 이름이 `${local.name}-ingest` 다. `iam.tf` 의
# `local.scheduled_task_definition_arn`(`task-definition/pokehelper-prod-*:*`)
# 이 이 접두사를 덮는다 — 그래서 스케줄러 역할이 **이 태스크만** 띄울 수 있다.
#
# ★ 스케줄은 `scheduler.tf` 에 있다. "무엇을 돌리는가"(여기)와 "언제 돌리는가"
#   (거기)를 나눠 둔다 — ADR-011 이 CLI 와 스케줄러를 나눈 것과 같은 선이다.
############################################################

resource "aws_ecs_task_definition" "ingest" {
  family = "${local.name}-ingest"

  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"

  # 수집은 외부 API 를 1초에 한 번 부르며 기다리는 일이라 CPU 를 거의 쓰지 않는다.
  # 2026-11-25 실측: 50종 수집에 55초, 대부분이 대기다.
  cpu    = 256
  memory = 512

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "ARM64"
  }

  execution_role_arn = aws_iam_role.ecs_execution.arn
  task_role_arn      = aws_iam_role.ecs_task.arn

  container_definitions = jsonencode([local.ingest_container])

  tags = { Name = "${local.name}-ingest" }
}

############################################################
# 서비스
#
# ★ `assign_public_ip = true` 가 **필수다** ★
#
# 퍼블릭 서브넷에 두기로 했으므로(ADR-020 결정 1=B) 공인 IP 가 없으면 태스크가
# 아무데도 못 나간다. 그리고 필요한 곳은 외부 API 만이 아니다 — **ECR 이미지
# pull, CloudWatch Logs 전송, SSM(ECS Exec) 채널**이 전부 인터넷 경로를 쓴다
# (ADR-020 세부사항 3). 빠뜨리면 `CannotPullContainerError` 로 떨어진다.
#
# `network.tf` 의 서브넷은 `map_public_ip_on_launch = false` 인데, awsvpc 태스크의
# 공인 IP 는 **서브넷 기본값이 아니라 이 설정**이 정한다. 둘은 별개다.
#
# ★ 컨테이너 수준 healthCheck 를 두지 않는다 ★
#
# ALB 타깃그룹 헬스체크가 이미 트래픽을 막고 교체를 유발한다(`alb.tf`,
# 15초 간격 / 3회 실패). 컨테이너 헬스체크를 더하면 같은 일을 두 번 하면서
# **실패 경로가 하나 늘어난다** — 체크 명령이 쓰는 바이너리가 이미지에 없으면
# 서비스는 멀쩡한데 태스크가 계속 교체된다.
# 되돌리는 조건: ALB 뒤에 없으면서 상시로 떠 있어야 하는 태스크가 생기면 그
# 태스크에는 컨테이너 헬스체크를 둔다 (일회성·스케줄 태스크는 끝나면 사라지므로
# 해당 없다).
############################################################

locals {
  # 두 서비스가 같은 네트워크 구성을 쓴다. `run-task` 에도 같은 값을 넘겨야
  # 하므로 출력으로도 내보낸다 (아래).
  task_subnet_ids = [for subnet in aws_subnet.public : subnet.id]
}

resource "aws_ecs_service" "backend" {
  name            = "${local.name}-backend"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.backend.arn
  desired_count   = 1
  launch_type     = "FARGATE"

  # ★ Fargate 에는 SSH 가 없다. 도는 컨테이너에 들어갈 유일한 수단이다.
  #   권한은 `iam.tf` 의 태스크 역할이 이미 갖고 있고(ssmmessages 4개),
  #   **이 스위치가 없으면 권한만 있고 열리지 않는다.**
  #   운영 DB 계정을 만들 수 있는 유일한 경로이기도 하다 (migrate 주석 참조).
  enable_execute_command = true

  network_configuration {
    subnets          = local.task_subnet_ids
    security_groups  = [aws_security_group.tasks.id]
    assign_public_ip = true # ★ 위 주석 — 빠뜨리면 이미지를 못 받는다
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.app["backend"].arn
    container_name   = "backend"
    container_port   = 8000
  }

  # ★ 부팅 중에 헬스체크로 죽이지 않는다. gunicorn 2 워커가 뜨고 첫 응답을
  #   줄 때까지의 시간이다. 짧게 두면 **뜨는 중인 태스크를 계속 교체**하면서
  #   영원히 안정되지 않는다.
  health_check_grace_period_seconds = 60

  # ★ 배포가 실패하면 되돌린다 ★
  #
  # 없으면 ECS 가 깨진 태스크를 **무한히 재시도**한다. 이미지가 잘못됐거나
  # 시크릿 키가 어긋났을 때 그 상태가 조용히 계속된다.
  # 첫 배포에는 되돌릴 이전 버전이 없어서 "실패"로 끝나는데, 그래도 무한
  # 재시도보다 낫다 — 실패는 사람이 본다.
  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }

  # 100/200 = 새 태스크를 먼저 띄우고 옛 것을 내린다. 0/100 으로 두면
  # 배포마다 **무중단이 깨진다** (태스크가 1개이므로 내리는 순간 응답이 없다).
  deployment_minimum_healthy_percent = 100
  deployment_maximum_percent         = 200

  # ★ apply 가 안정화를 기다리지 않는다(기본값).
  #   기다리게 하면 첫 배포에서 10분 넘게 멈춰 있고, 실패해도 원인이
  #   Terraform 출력에 안 나온다. `aws ecs describe-services` 와 로그 그룹을
  #   보는 편이 빠르다. 절차는 README 5-E.
  wait_for_steady_state = false

  # 리스너가 먼저 있어야 타깃그룹에 등록할 수 있다.
  depends_on = [aws_lb_listener.https]

  tags = { Name = "${local.name}-backend" }
}

resource "aws_ecs_service" "frontend" {
  name            = "${local.name}-frontend"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.frontend.arn
  desired_count   = 1
  launch_type     = "FARGATE"

  enable_execute_command = true

  network_configuration {
    subnets          = local.task_subnet_ids
    security_groups  = [aws_security_group.tasks.id]
    assign_public_ip = true
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.app["frontend"].arn
    container_name   = "frontend"
    container_port   = 3000
  }

  health_check_grace_period_seconds = 60

  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }

  deployment_minimum_healthy_percent = 100
  deployment_maximum_percent         = 200

  wait_for_steady_state = false

  depends_on = [aws_lb_listener.https]

  tags = { Name = "${local.name}-frontend" }
}

############################################################
# 출력
#
# ★ `run-task` 에 넘길 값을 여기서 내보낸다. 손으로 서브넷 ID 를 적게 하면
#   거기서 틀린다 — 프라이빗 서브넷을 적으면 태스크가 이미지를 못 받고,
#   메시지는 서브넷을 가리키지 않는다.
############################################################

output "ecs_cluster_name" {
  description = "★ iam.tf 의 스케줄러 정책이 가정하는 이름과 같아야 한다."
  value       = aws_ecs_cluster.main.name
}

output "ecs_service_names" {
  description = "aws ecs describe-services / execute-command 에 넣는다."
  value = {
    backend  = aws_ecs_service.backend.name
    frontend = aws_ecs_service.frontend.name
  }
}

output "migrate_task_family" {
  description = "스키마 적용과 시드 적재를 돌리는 일회성 태스크 정의. command 는 --overrides 로 바꾼다."
  value       = aws_ecs_task_definition.migrate.family
}

output "task_network_configuration" {
  description = <<-EOT
    `aws ecs run-task --network-configuration` 에 그대로 넣는 값.

    awsvpcConfiguration={subnets=[...],securityGroups=[...],assignPublicIp=ENABLED}

    ★ assignPublicIp 를 빼면 이미지를 못 받는다. 퍼블릭 서브넷에 두기로 한
      결정(ADR-020 결정 1)의 귀결이다.
  EOT
  value = {
    subnets          = local.task_subnet_ids
    security_groups  = [aws_security_group.tasks.id]
    assign_public_ip = "ENABLED"
  }
}
