############################################################
# Application Load Balancer
#
# ADR-020 이 고른 구성: 퍼블릭 서브넷의 ALB 하나가 **유일한 인터넷 입구**이고,
# 태스크는 공인 IP 를 달지만 인바운드는 ALB 보안그룹에서만 받는다.
# 경로 기반 라우팅이다 — `/api/*` → 백엔드, `/*` → 프론트 (ADR-020 구성도).
#
# ★ 월 ~$22 다 (기본 $16.43 + LCU 1개 $5.84). **만든 시점부터 과금**이고
#   트래픽이 0 이어도 기본료는 나간다. 가장 비싼 단일 자원이다.
############################################################

resource "aws_lb" "main" {
  name               = "${local.name}-alb"
  load_balancer_type = "application"

  # 인터넷에서 닿아야 한다. `true` 면 VPC 내부 전용이 되고, 그러면 ALB 를 둘
  # 이유가 없다.
  internal = false

  security_groups = [aws_security_group.alb.id]

  # ★ 퍼블릭 서브넷 **두 AZ 모두** 필요하다. ALB 는 최소 2개 AZ 를 요구한다.
  #   한 개만 주면 `At least two subnets in two different Availability Zones`
  #   로 거부된다 — 가용성 선택이 아니라 **제약**이다.
  subnets = [for subnet in aws_subnet.public : subnet.id]

  # ★ 헤더 위조 차단 ★
  # HTTP 규격에 맞지 않는 헤더를 ALB 가 **버린다.** 끄면 그대로 뒤로 넘어가고,
  # 뒤의 프레임워크가 그걸 어떻게 해석하는지에 보안이 걸리게 된다
  # (`X-Forwarded-For` 를 두 번 보내는 식의 공격). 기본값이 아니라서 명시한다.
  drop_invalid_header_fields = true

  # ★ IPv4 전용이다. 바꾸려면 `dns.tf` 의 AAAA 레코드와 **같이** 바꿔야 한다.
  #   한쪽만 바꾸면 "레코드는 있는데 연결이 안 된다"가 된다.
  ip_address_type = "ipv4"

  # ★ RDS 와 **다르게** 삭제 보호를 걸지 않는다 ★
  # RDS 는 데이터를 들고 있어서 실수로 사라지면 복구가 유일한 수단이다. ALB 는
  # **상태가 없다** — 지워지면 몇 분 만에 같은 구성으로 다시 만들 수 있고,
  # 바뀌는 건 DNS 이름뿐인데 그건 alias 레코드가 따라간다. 모든 자원에 보호를
  # 거는 것은 보호를 의식에서 지우는 가장 빠른 길이다.
  enable_deletion_protection = false

  # ★ 액세스 로그를 켜지 않았다 ★
  # S3 버킷이 추가로 필요하고 요청마다 저장 비용이 붙는다. ADR-020 결정 5 가
  # 로그 비용을 줄이는 쪽을 골랐고, 지금 필요한 관측은 Prometheus·Grafana
  # 사이드카가 맡는다. **접근 패턴 조사가 필요해지면 그때 켠다** — 그게
  # 이 선택을 되돌리는 조건이다.

  tags = { Name = "${local.name}-alb" }
}

############################################################
# 타깃 그룹
############################################################

locals {
  # ★ 이름은 32자 제한이다. `pokehelper-prod-frontend` = 24자로 여유가 있지만,
  #   `project`·`environment` 를 길게 바꾸면 조용히 넘친다 (테스트가 본다).
  target_groups = {
    backend = {
      port              = 8000
      health_check_path = "/health"
      purpose           = "FastAPI"
    }
    frontend = {
      port              = 3000
      health_check_path = "/"
      purpose           = "Next.js SSR"
    }
    grafana = {
      port              = 3001
      health_check_path = "/api/health"
      purpose           = "Grafana sidecar"
    }
  }
}

resource "aws_lb_target_group" "app" {
  for_each = local.target_groups

  name     = "${local.name}-${each.key}"
  port     = each.value.port
  protocol = "HTTP"
  vpc_id   = aws_vpc.main.id

  # ★ Fargate 는 `ip` 여야 한다 ★
  # awsvpc 네트워크 모드에서 태스크는 ENI 를 직접 들고 뜬다. `instance` 는
  # EC2 기반 ECS 용이고, Fargate 에 쓰면 등록 자체가 안 된다.
  target_type = "ip"

  # ★ 기본값 300초를 30초로 줄인다 ★
  # 배포 때 옛 태스크를 등록 해제하면서 ALB 가 **이 시간만큼 기다린다.** 기본값
  # 이면 배포 한 번에 5분이 그냥 지나간다. 긴 요청을 끊지 않으려는 값인데,
  # 우리 요청은 계산 API 라 수백 ms 다. 30초는 그보다 두 자리 크다.
  deregistration_delay = 30

  health_check {
    enabled = true
    path    = each.value.health_check_path

    # ★ 아래 네 값이 "고장을 언제 알아채는가"를 정한다 ★
    #   비정상 판정까지: interval × unhealthy_threshold = 15 × 3 = 45초
    #   정상 복귀까지:   interval × healthy_threshold   = 15 × 2 = 30초
    # 간격을 짧게 하면 빨리 알지만 헬스체크 요청이 늘고, 그 요청도 과금 대상
    # LCU 에 들어간다. 15초면 한 태스크당 분당 4회다.
    interval            = 15
    timeout             = 5
    healthy_threshold   = 2
    unhealthy_threshold = 3

    # 200 만 통과시킨다. 범위를 넓히면 "500 을 돌려주면서 정상"이 가능해진다.
    matcher = "200"
  }

  tags = { Name = "${local.name}-${each.key}", Purpose = each.value.purpose }
}

############################################################
# 리스너 — HTTPS
############################################################

resource "aws_lb_listener" "https" {
  load_balancer_arn = aws_lb.main.arn
  port              = 443
  protocol          = "HTTPS"

  # TLS 1.3 + 1.2. 1.0/1.1 을 받지 않는다.
  ssl_policy = "ELBSecurityPolicy-TLS13-1-2-2021-06"

  # ★ `aws_acm_certificate.main.arn` 이 아니라 **validation 쪽**을 참조한다 ★
  # 값은 같지만 의존 관계가 다르다. 인증서 자원만 참조하면 Terraform 이
  # `Pending validation` 상태의 인증서를 리스너에 붙이려 하고, 그러면
  # `CertificateNotFound` 로 깨진다. validation 자원을 참조하면 **발급 완료가
  # 선행 조건**이 된다. 이게 ACM + ALB 조합에서 가장 흔한 실수다.
  certificate_arn = aws_acm_certificate_validation.main.certificate_arn

  # 규칙에 걸리지 않은 전부는 프론트로 간다 (ADR-020 의 `/*`).
  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.app["frontend"].arn
  }

  tags = { Name = "${local.name}-https" }
}

# ★ `/health` 를 백엔드 규칙에 **같이** 넣은 이유 ★
# ADR-020 의 라우팅은 `/api/*` → 백엔드인데, FastAPI 의 헬스 라우터는 접두사가
# 없어서 경로가 `/health` 다 (app/api/v1/health.py). 규칙이 `/api/*` 뿐이면
# `/health` 는 프론트로 가서 404 가 되고, **README 4장의 수락 기준인
# "`/health` 가 200 인가"를 외부에서 확인할 수 없다.**
#
# 공개해도 되는 이유: 응답이 `status`·`service`·`version` 뿐이다. 다만
# `version` 은 노출된다 — 지금은 소스에 박힌 "0.1.0" 이다.
#
# ★ 반대로 `/metrics` 는 **일부러 라우팅하지 않는다.** 경로가 `/metrics` 라
#   `/api/*` 에 걸리지 않고 프론트로 가서 404 가 된다. ADR-019 의
#   `METRICS_TOKEN` 은 그 위에 한 겹 더 두는 방어다 — 토큰이 유일한 방어선이
#   아니게 된다. Prometheus 사이드카는 **같은 태스크 안에서 localhost** 로
#   긁으므로 ALB 를 거치지 않는다.
resource "aws_lb_listener_rule" "backend" {
  listener_arn = aws_lb_listener.https.arn
  priority     = 100

  action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.app["backend"].arn
  }

  condition {
    path_pattern {
      values = ["/api/*", "/health"]
    }
  }

  tags = { Name = "${local.name}-rule-backend" }
}

# ★ `/grafana` 와 `/grafana/*` 를 **둘 다** 적는다. ALB 의 경로 패턴에서
#   `/grafana/*` 는 `/grafana`(슬래시 없는 그 자체)에 매칭되지 않는다.
#   하나만 적으면 "끝에 슬래시를 붙이면 되는데 안 붙이면 404" 가 된다.
#
# ★ ecs.tf 가 같이 맞춰야 하는 것: Grafana 를 하위 경로에서 서비스하려면
#   `GF_SERVER_ROOT_URL` 과 `GF_SERVER_SERVE_FROM_SUB_PATH=true` 가 필요하다.
#   빠뜨리면 로그인 화면은 뜨는데 **리다이렉트가 루트로 가서 프론트로 떨어진다.**
#
# ★ 이 경로는 ADR-020 이 명시하지 않았다. 구성도에는 Grafana 포트(3001)만 있고
#   경로가 없다. `security_groups.tf` 에 "ALB to Grafana sidecar" 규칙이 이미
#   있으므로 ALB 를 거치는 것은 결정돼 있고, **경로 이름만 여기서 정했다.**
resource "aws_lb_listener_rule" "grafana" {
  listener_arn = aws_lb_listener.https.arn
  priority     = 200

  action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.app["grafana"].arn
  }

  condition {
    path_pattern {
      values = ["/grafana", "/grafana/*"]
    }
  }

  tags = { Name = "${local.name}-rule-grafana" }
}

############################################################
# 리스너 — HTTP 는 리다이렉트만 한다
############################################################

# ★ 80 을 닫지 않고 리다이렉트하는 이유 ★
# 닫으면 `http://도메인` 이 **연결 거부**가 된다. 주소창에 도메인만 치면
# 브라우저가 http 로 먼저 가므로, 사용자에게는 "사이트가 없다"로 보인다.
# 301(영구)을 쓰면 브라우저가 다음부터 바로 https 로 간다.
#
# 보안그룹에도 80 이 열려 있다 (`security_groups.tf`: "Internet to ALB (HTTP).
# Listener redirects permanently to 443."). 두 파일이 같은 결정을 가리킨다.
resource "aws_lb_listener" "http" {
  load_balancer_arn = aws_lb.main.arn
  port              = 80
  protocol          = "HTTP"

  default_action {
    type = "redirect"

    redirect {
      port        = "443"
      protocol    = "HTTPS"
      status_code = "HTTP_301"
      # host·path·query 는 적지 않는다 — 생략하면 원래 요청 값을 그대로
      # 유지한다. `/api/v1/calc` 로 온 http 요청이 같은 경로의 https 로 간다.
    }
  }

  tags = { Name = "${local.name}-http" }
}

############################################################
# 출력
############################################################

output "alb_dns_name" {
  description = "ALB 자체 주소. 도메인이 아직 안 퍼졌을 때 여기로 먼저 확인한다."
  value       = aws_lb.main.dns_name
}

output "target_group_arns" {
  description = "ecs.tf 의 서비스가 `load_balancer` 블록에서 참조한다."
  value       = { for key, group in aws_lb_target_group.app : key => group.arn }
}
