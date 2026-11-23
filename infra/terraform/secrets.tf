############################################################
# 운영 시크릿 (ADR-022)
#
# ★★ 값은 이 코드에도, plan 에도, state 에도 없다 ★★
#
# Terraform 은 **시크릿 껍데기만** 만든다. 값은 사람이 한 번 넣는다
# (`aws secretsmanager put-secret-value`, 절차는 README 5-C).
#
# ★ `aws_secretsmanager_secret_version` 을 쓰지 않는 것이 이 파일의 전부다.
#   그 자원을 추가하면 값이 **state 에 평문으로** 들어가고, 이 설계가 통째로
#   무의미해진다. `tests/integration/test_terraform_secrets.py` 가 그걸 막는다.
#
# ★ SSM 파라미터 스토어를 쓰지 않은 이유 ★
#
# SSM 쪽이 무료라 먼저 그걸로 설계했는데, `ignore_changes = [value]` 가
# **값을 state 에서 막아주지 않는다.** ignore_changes 는 plan 시점의 동작만
# 바꾸고, `refresh` 는 실제 객체에 맞춰 state 를 다시 쓴다. 즉 손으로 넣은
# 실제 값이 다음 refresh 에서 state 에 평문으로 들어온다. 게다가 state 버킷은
# **버전 관리가 켜져 있어서**(bootstrap/main.tf) 한 번 들어간 값이 구 버전에
# 영구히 남는다. 지우기가 더 어렵다.
# (AWS 프로바이더 이슈 #3475 — SecureString 을 state 에 저장하지 말라는 요청이
#  아직 열려 있다.)
############################################################

locals {
  # ★ 키 이름 = 컨테이너에 주입될 **환경변수 이름 그대로**.
  #
  # 그래서 ecs.tf 의 매핑이 기계적이 된다 — `name = key`,
  # `valueFrom = "${arn}:${key}::"`. 이름을 다르게 두면 매핑 표를 손으로
  # 관리해야 하고, 거기서 틀리면 **앱이 기본값으로 조용히 뜬다.**
  #
  # 하나의 시크릿에 JSON 으로 담는다. ECS 는 `valueFrom` 끝에 JSON 키를
  # 붙여 **키 하나만** 꺼낼 수 있다. 6개를 따로 만들면 월 $2.40, 하나면 $0.40 다.
  app_secret_keys = {
    # ── 앱 ────────────────────────────────────────────────
    # 앱 런타임 계정. DML 만 가진다 (ADR-015).
    # ★ 접속 주소가 이 안에 들어간다. PITR 로 복구하면 **엔드포인트가 바뀌므로**
    #   이 값도 다시 넣어야 한다. 안 바꾸면 복구는 성공했는데 앱이 옛 주소를
    #   계속 보고 있다 (ADR-020 의 RTO 를 늘리는 함정).
    DATABASE_URL = "App runtime DSN (champs_app, DML only)"
    # alembic 만 읽는다. DDL 을 가진 유일한 계정이다.
    MIGRATION_DATABASE_URL = "Alembic DSN (champs_migrate, DDL)"
    # ★ 비어 있으면 운영에서 **기동을 거부한다** (ADR-019,
    #   assert_production_ready). 기본값이 "열림"인 설정이라 토큰이 유일한 문이다.
    METRICS_TOKEN = "Bearer token guarding /metrics"

    # ── 관측 사이드카 (ADR-016 / ADR-020 결정 5) ──────────
    # postgres-exporter 가 쓴다. 읽기 전용 계정이다.
    DATA_SOURCE_NAME = "postgres-exporter DSN (champs_read, read only)"
    # ★ 로컬 기본값은 admin/admin 이다 (의도된 것, ADR-016 포기한 것).
    #   운영에서 그 값이 남으면 대시보드가 내부 지연·오류율을 그대로 보여준다.
    #   사용자명도 자격증명의 절반이라 같이 넣는다.
    GF_SECURITY_ADMIN_USER     = "Grafana admin user"
    GF_SECURITY_ADMIN_PASSWORD = "Grafana admin password"
  }
}

resource "aws_secretsmanager_secret" "app" {
  name        = "${local.name}/app"
  description = "Runtime secrets injected into ECS tasks. Values are set by hand, never by Terraform (ADR-022)."

  # ★ 0 = 즉시 삭제. 7~30 을 쓰면 `destroy` 후 같은 이름으로 `apply` 가
  #   **"already scheduled for deletion"** 으로 막힌다. 최소값이 7일이라 그
  #   기간 동안 재구축이 불가능해진다. IaC 왕복(destroy → apply)이 성립해야
  #   하는 것이 더 중요하다 — `ecr.tf` 의 `force_delete` 와 같은 판단이다.
  #
  #   ★ 대신 되돌릴 수 없다. 지우면 값은 사라지고 다시 손으로 넣어야 한다.
  #     사람이 넣은 값이라 다시 만들 수 있고, 데이터가 아니다.
  recovery_window_in_days = 0

  # kms_key_id 를 지정하지 않는다 → AWS 관리형 `aws/secretsmanager` 키를 쓴다.
  # 고객 관리형 키는 월 $1 이고, 키 정책을 우리가 쓸 일이 지금은 없다.

  tags = { Name = "${local.name}-app-secrets" }
}

############################################################
# 출력 — 값을 넣을 때 쓴다
#
# ★ 복사용 블록에 **가린 값을 넣지 않는다.** 키 목록을 여기서 내보내고,
#   README 의 절차가 이걸 템플릿으로 받아 쓴다. 명령에 값을 손으로 적지
#   않으므로 오타로 키가 어긋날 일이 없다.
############################################################

output "app_secret_name" {
  description = "put-secret-value 의 --secret-id 에 넣을 이름."
  value       = aws_secretsmanager_secret.app.name
}

output "app_secret_arn" {
  description = "ecs.tf 가 valueFrom 에 쓴다. ARN 뒤에 ':<키>::' 를 붙여 키 하나만 꺼낸다."
  value       = aws_secretsmanager_secret.app.arn
}

output "app_secret_template" {
  description = <<-EOT
    값이 빈 JSON 템플릿. 이걸 받아 값을 채워 넣는다 (README 5-C).

    ★ 이 출력에는 시크릿이 없다 — 키와 빈 문자열뿐이다.
  EOT
  value       = { for key in keys(local.app_secret_keys) : key => "" }
}

output "app_secret_keys_documented" {
  description = "각 키가 무엇인지. 사람이 값을 채울 때 읽는다."
  value       = local.app_secret_keys
}
