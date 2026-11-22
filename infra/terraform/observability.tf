############################################################
# 비용 알림 (ADR-020 세부사항 8)
#
# ★ 비용 절감 장치가 아니라 사고 방지 장치다 ★
#
# 로그 폭증, 데이터 전송 증가, 지우지 않은 자원 — 전부 **조용히** 늘어난다.
# 알림이 없으면 월말 청구서로 처음 안다. 그때는 이미 한 달치다.
#
# ★ CloudWatch 청구 알람이 아니라 AWS Budgets 를 쓴다 ★
#
# ADR-020 세부사항 8은 "CloudWatch 청구 알람"으로 적혀 있었다. 구현하면서
# 조사해보니 Budgets 가 모든 면에서 낫다 (ADR-020 결과에 기록).
#
#   - **예측 알림이 된다.** CloudWatch 청구 알람은 AWS 문서 표현대로
#     "triggers only when the current billing exceeds the threshold.
#      It doesn't use projections" — **이미 돈이 나간 뒤에만** 울린다.
#     사고 방지 장치로는 반쪽이다.
#   - **무료다.** CloudWatch 알람은 $0.10/개·월.
#   - **사전 활성화가 필요 없다.** CloudWatch 쪽은 "Receive CloudWatch Billing
#     Alerts" 를 켜야 하고 **한 번 켜면 되돌릴 수 없다.**
#   - **리전 제약이 없다.** 청구 지표는 us-east-1 에만 올라와서 프로바이더
#     별칭이 하나 더 필요했을 것이다.
############################################################

resource "aws_budgets_budget" "monthly" {
  name         = "${local.name}-monthly"
  budget_type  = "COST"
  limit_amount = tostring(var.monthly_budget_usd)
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  # ★ 임계치를 셋으로 나눈 이유 ★
  #
  # 하나만 두면 그 하나가 두 가지 일을 다 해야 한다. 낮게 잡으면 평소에 울려서
  # 사람이 무시하는 법을 배우고, 높게 잡으면 알았을 때 이미 늦다.
  # ADR-016 에서 경보를 둘로 나눈 것과 같은 이유다.
  #
  # 예산 하나에 알림 여러 개를 거는 것은 추가 비용이 없다.

  # ── 1. 경고 — "예상보다 빨리 쓰고 있다" ──────────────────
  # 기본값 150 기준 $30. 아직 자원이 적을 때는 이 선을 넘는 것 자체가 신호다.
  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 20
    threshold_type             = "PERCENTAGE"
    notification_type          = "ACTUAL"
    subscriber_email_addresses = [var.alarm_email]
  }

  # ── 2. 임박 — "한도에 가까워진다" ────────────────────────
  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 80
    threshold_type             = "PERCENTAGE"
    notification_type          = "ACTUAL"
    subscriber_email_addresses = [var.alarm_email]
  }

  # ── 3. ★ 예측 — "이대로 가면 월말에 초과한다" ────────────
  #
  # 이게 Budgets 를 고른 이유다. 위 둘은 **이미 쓴 돈**을 보고, 이것만이
  # **앞으로 쓸 돈**을 본다. 월초에 NAT 를 실수로 켰다면 실제 비용이
  # 한도에 닿기 한참 전에 이 알림이 먼저 온다.
  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 100
    threshold_type             = "PERCENTAGE"
    notification_type          = "FORECASTED"
    subscriber_email_addresses = [var.alarm_email]
  }
}

# ★ Budgets 는 하루 최대 3번 갱신된다. 즉 **실시간이 아니다.**
#   청구가 발생하고 알림이 오기까지 지연이 있다. 이 알림은 "급브레이크"가
#   아니라 "계기판"이다 — 급한 차단이 필요하면 예산 작업(budget action)이나
#   서비스 쿼터를 따로 봐야 하고, 그건 지금 범위가 아니다.

output "budget_name" {
  description = "월 비용 예산. 콘솔 → Billing and Cost Management → Budgets 에서 보인다."
  value       = aws_budgets_budget.monthly.name
}
