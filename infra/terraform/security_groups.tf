############################################################
# 보안그룹 (ADR-020 결정 1, 세부사항 1·2)
#
# ★ 이 파일이 B안(퍼블릭 서브넷)의 **유일한 방어선**이다.
#
# 태스크가 퍼블릭 서브넷에 공인 IP 를 달고 떠 있으므로, 프라이빗 서브넷처럼
# "라우팅이 없어서 못 닿는다"가 성립하지 않는다. 여기 한 줄이 틀리면 백엔드가
# 인터넷에 열린다. 그리고 **열려도 서비스는 멀쩡히 돈다** — 사람이 못 잡는다.
#
# 그래서 규칙을 둘로 나눠 집행한다.
#   1. 인바운드 소스를 CIDR 이 아니라 **다른 보안그룹 ID** 로 지정한다.
#   2. `0.0.0.0/0` 인바운드는 ALB 보안그룹에만 허용하고,
#      `tests/integration/test_terraform_security_groups.py` 가 그것을 단정한다.
#
# ★ `description` 은 영문이다 — AWS 가 ASCII 만 받는다 ★
#
# EC2 의 `GroupDescription` 과 규칙 `Description` 은 ASCII 전용이다. 한글을
# 넣으면 **plan 은 통과하고 apply 가 400 으로 터진다** — 그래서 절반만 만들어진
# 인프라가 남는다 (JOURNAL 2026-11-21). 주석은 한글로 쓰되 **AWS 로 넘어가는
# 문자열만** 영문으로 둔다. `tests/integration/test_terraform_security_groups.py`
# 의 `TestDescriptionsAreAscii` 가 집행한다.
#
# ★ 인라인 `ingress {}` 블록을 쓰지 않는다. 별도 리소스
#   (`aws_vpc_security_group_ingress_rule`)로만 쓴다. 인라인 블록을 섞으면
#   테스트가 보지 못하는 경로가 생기고, 두 방식을 한 보안그룹에 섞으면
#   Terraform 이 서로의 규칙을 지운다. 테스트가 인라인 블록의 부재도 단정한다.
############################################################

############################################################
# ALB — 인터넷을 마주하는 **유일한** 자원
############################################################

resource "aws_security_group" "alb" {
  name        = "${local.name}-alb"
  description = "ALB. The only group that accepts traffic from the internet."
  vpc_id      = aws_vpc.main.id

  tags = { Name = "${local.name}-alb", Exposure = "internet" }
}

resource "aws_vpc_security_group_ingress_rule" "alb_https" {
  security_group_id = aws_security_group.alb.id
  description       = "Internet to ALB (HTTPS)"
  cidr_ipv4         = "0.0.0.0/0"
  from_port         = 443
  to_port           = 443
  ip_protocol       = "tcp"
}

resource "aws_vpc_security_group_ingress_rule" "alb_http_redirect" {
  security_group_id = aws_security_group.alb.id
  description       = "Internet to ALB (HTTP). Listener redirects permanently to 443."
  cidr_ipv4         = "0.0.0.0/0"
  from_port         = 80
  to_port           = 80
  ip_protocol       = "tcp"
}

# ALB 는 타깃(태스크)에만 나간다. 인터넷으로 나갈 일이 없다.
resource "aws_vpc_security_group_egress_rule" "alb_to_tasks" {
  security_group_id            = aws_security_group.alb.id
  description                  = "ALB to tasks"
  referenced_security_group_id = aws_security_group.tasks.id
  from_port                    = 3000
  to_port                      = 8000
  ip_protocol                  = "tcp"
}

############################################################
# ECS 태스크 — 퍼블릭 서브넷, 공인 IP 있음
############################################################

resource "aws_security_group" "tasks" {
  name        = "${local.name}-tasks"
  description = "ECS tasks. Public IP attached, but inbound only from the ALB."
  vpc_id      = aws_vpc.main.id

  tags = { Name = "${local.name}-tasks", Exposure = "alb-only" }
}

resource "aws_vpc_security_group_ingress_rule" "tasks_from_alb_backend" {
  security_group_id            = aws_security_group.tasks.id
  description                  = "ALB to backend (FastAPI)"
  referenced_security_group_id = aws_security_group.alb.id
  from_port                    = 8000
  to_port                      = 8000
  ip_protocol                  = "tcp"
}

resource "aws_vpc_security_group_ingress_rule" "tasks_from_alb_frontend" {
  security_group_id            = aws_security_group.tasks.id
  description                  = "ALB to frontend (Next.js)"
  referenced_security_group_id = aws_security_group.alb.id
  from_port                    = 3000
  to_port                      = 3000
  ip_protocol                  = "tcp"
}

resource "aws_vpc_security_group_ingress_rule" "tasks_from_alb_grafana" {
  security_group_id            = aws_security_group.tasks.id
  description                  = "ALB to Grafana sidecar"
  referenced_security_group_id = aws_security_group.alb.id
  from_port                    = 3001
  to_port                      = 3001
  ip_protocol                  = "tcp"
}

# ★ Prometheus(9090)는 없다. 사이드카가 **같은 태스크 안에서 localhost 로**
#   백엔드를 긁으므로, 태스크 밖에서 Prometheus 에 닿을 이유가 없다.
#   보안그룹은 태스크 **사이**를 통제하지 컨테이너 사이를 통제하지 않는다 —
#   같은 태스크의 컨테이너끼리는 loopback 으로 통신하고 규칙을 타지 않는다.

# ★ 아웃바운드는 전부 연다. 좁히지 않은 이유를 적어둔다.
#
#   태스크가 나가야 하는 곳: ECR(443), CloudWatch Logs(443), SSM(443),
#   championsbattledata.com(443), VPC DNS 리졸버(UDP 53), RDS(5432),
#   ElastiCache(6379). 이 중 하나라도 빠뜨리면 **기동은 되는데 조용히
#   실패한다** — 이미지 pull 실패는 눈에 띄지만 DNS 나 로그 전송 실패는 아니다.
#
#   아웃바운드를 여는 것은 인바운드를 여는 것과 위험이 다르다. 외부에서
#   들어올 수 있는 길이 늘어나지 않는다. 그래서 테스트도 인바운드만 본다.
#   좁히는 것은 실제로 나가는 곳을 VPC 플로우 로그로 측정한 뒤에 한다.
resource "aws_vpc_security_group_egress_rule" "tasks_all" {
  security_group_id = aws_security_group.tasks.id
  description       = "Tasks to anywhere (ECR, SSM, logs, external API, DNS)"
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "-1"
}

############################################################
# RDS — 프라이빗 서브넷
############################################################

resource "aws_security_group" "rds" {
  name        = "${local.name}-rds"
  description = "RDS PostgreSQL. Inbound only from the backend tasks."
  vpc_id      = aws_vpc.main.id

  tags = { Name = "${local.name}-rds", Exposure = "vpc-internal" }
}

resource "aws_vpc_security_group_ingress_rule" "rds_from_tasks" {
  security_group_id            = aws_security_group.rds.id
  description                  = "Tasks to PostgreSQL"
  referenced_security_group_id = aws_security_group.tasks.id
  from_port                    = 5432
  to_port                      = 5432
  ip_protocol                  = "tcp"
}

# ★ 아웃바운드 규칙이 없다. RDS 는 먼저 연결을 걸지 않는다.
#   Terraform 의 `aws_security_group` 은 인라인 egress 를 쓰지 않으면
#   기본 허용 규칙을 만들지 않으므로, 적지 않는 것이 곧 차단이다.

############################################################
# ElastiCache — 프라이빗 서브넷
############################################################

resource "aws_security_group" "cache" {
  name        = "${local.name}-cache"
  description = "ElastiCache Redis. Inbound only from the backend tasks."
  vpc_id      = aws_vpc.main.id

  tags = { Name = "${local.name}-cache", Exposure = "vpc-internal" }
}

resource "aws_vpc_security_group_ingress_rule" "cache_from_tasks" {
  security_group_id            = aws_security_group.cache.id
  description                  = "Tasks to Redis"
  referenced_security_group_id = aws_security_group.tasks.id
  from_port                    = 6379
  to_port                      = 6379
  ip_protocol                  = "tcp"
}
