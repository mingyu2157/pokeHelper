############################################################
# 네트워크 (ADR-020 결정 1)
#
#   퍼블릭 서브넷  : ALB, ECS 태스크(공인 IP), 수집 태스크
#   프라이빗 서브넷: RDS, ElastiCache  ← 인터넷 경로가 **없다**
#
# ★ NAT Gateway 가 없다. 의도적이다.
#
# 태스크를 퍼블릭 서브넷에 두고 공인 IP 를 붙여 월 $43.07 을 아낀다
# (공인 IP $3.65/태스크). 대신 **방어선이 보안그룹 하나뿐**이고, 그
# 보안그룹이 맞는지는 `tests/integration/test_terraform_security_groups.py`
# 가 지킨다.
#
# 프라이빗 서브넷에 NAT 가 없으므로 **RDS·ElastiCache 는 인터넷에 못 나간다.**
# 둘 다 나갈 일이 없으므로 의도한 상태다. 여기에 인터넷이 필요한 것을 넣으면
# 조용히 멈춘다.
############################################################

resource "aws_vpc" "main" {
  cidr_block           = var.vpc_cidr
  enable_dns_support   = true
  enable_dns_hostnames = true # RDS 엔드포인트 이름 해석에 필요하다

  tags = { Name = local.name }
}

resource "aws_internet_gateway" "main" {
  vpc_id = aws_vpc.main.id

  tags = { Name = local.name }
}

############################################################
# 서브넷
############################################################

resource "aws_subnet" "public" {
  for_each = { for index, az in local.azs : az => index }

  vpc_id            = aws_vpc.main.id
  availability_zone = each.key
  cidr_block        = cidrsubnet(var.vpc_cidr, 8, each.value) # 10.0.0.0/24, 10.0.1.0/24

  # ★ false 다. 서브넷 기본값으로 공인 IP 를 주지 않고, **ECS 서비스에서
  #   태스크 단위로 켠다**(`assign_public_ip`). 기본값으로 켜두면 나중에 여기
  #   무언가를 띄울 때 의도치 않게 공인 IP 가 붙는다.
  map_public_ip_on_launch = false

  tags = { Name = "${local.name}-public-${each.key}", Tier = "public" }
}

resource "aws_subnet" "private" {
  for_each = { for index, az in local.azs : az => index }

  vpc_id            = aws_vpc.main.id
  availability_zone = each.key
  cidr_block        = cidrsubnet(var.vpc_cidr, 8, each.value + 10) # 10.0.10.0/24, 10.0.11.0/24

  tags = { Name = "${local.name}-private-${each.key}", Tier = "private" }
}

############################################################
# 라우팅
############################################################

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.main.id

  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.main.id
  }

  tags = { Name = "${local.name}-public" }
}

resource "aws_route_table_association" "public" {
  for_each = aws_subnet.public

  subnet_id      = each.value.id
  route_table_id = aws_route_table.public.id
}

# ★ 프라이빗 라우트 테이블에는 0.0.0.0/0 경로가 없다.
#   VPC 로컬 통신만 된다. 이게 NAT 를 두지 않은 결과다.
resource "aws_route_table" "private" {
  vpc_id = aws_vpc.main.id

  tags = { Name = "${local.name}-private" }
}

resource "aws_route_table_association" "private" {
  for_each = aws_subnet.private

  subnet_id      = each.value.id
  route_table_id = aws_route_table.private.id
}
