############################################################
# DNS · TLS 인증서
#
# ★ 도메인 **등록**은 이 파일에 없다 ★
# 1년 약정이고 `terraform destroy` 로 되돌아가지 않으며, 등록자 연락처(개인정보)를
# 넣어야 한다. 콘솔에서 등록한 뒤 `domain_name` 과 `hosted_zone_id` 를 받아온다
# (`infra/terraform/README.md` 3장). **여기 있는 것은 등록 이후의 전부**다 —
# 인증서 발급, 검증 레코드, 서비스 A 레코드는 모두 Terraform 이 만든다.
#
# ★ 인증서 리전 = ALB 리전 (`ap-northeast-2`) ★
# `us-east-1` 고정은 **CloudFront 를 쓸 때**다. 우리는 ALB 에 직접 붙이므로
# ALB 와 같은 리전에 발급해야 한다. 리전이 어긋나면 ALB 리스너가 인증서를
# 찾지 못하고, 메시지는 "certificate not found" 라서 리전을 의심하기 어렵다.
#
# ★ 호스팅 존을 Terraform 으로 만들지 않는 이유 ★
# Route 53 에서 도메인을 등록하면 **퍼블릭 호스팅 존이 자동으로 생긴다.**
# 여기서 또 만들면 같은 이름의 존이 둘이 되고, 레지스트리가 가리키는 NS 는
# 처음 것이라 **우리가 만든 존에 넣은 레코드는 아무도 보지 못한다.**
# 증상이 "레코드는 있는데 조회가 안 된다"로 나와서 추적이 길다.
############################################################

############################################################
# 인증서
############################################################

resource "aws_acm_certificate" "main" {
  domain_name = var.domain_name

  # 이메일 검증을 쓰지 않는다. 사람이 메일을 눌러야 하고, 갱신마다 다시 눌러야
  # 한다. DNS 검증은 레코드가 남아 있는 한 **ACM 이 알아서 갱신**한다.
  validation_method = "DNS"

  # ★ 인증서를 교체할 때 순서를 뒤집는다 ★
  # ALB 리스너가 쓰고 있는 인증서는 지울 수 없다. 기본 순서(지우고→만들기)면
  # apply 가 `ResourceInUseException` 으로 막힌다. 새것을 먼저 만들고 리스너를
  # 옮긴 뒤 옛것을 지운다.
  lifecycle {
    create_before_destroy = true
  }

  tags = { Name = "${local.name}-cert" }
}

############################################################
# 검증 레코드 — ACM 이 요구하는 CNAME 을 호스팅 존에 넣는다
############################################################

# ★ 반드시 **퍼블릭** 호스팅 존이어야 한다. ACM 은 인터넷에서 조회하므로
#   프라이빗 존에 넣으면 영원히 `Pending validation` 이다.
resource "aws_route53_record" "cert_validation" {
  # domain_name 하나뿐이라 지금은 원소가 1개다. SAN(`www.` 등)을 추가하면
  # 자동으로 늘어난다 — 그때 이 블록을 고칠 일이 없다.
  for_each = {
    for option in aws_acm_certificate.main.domain_validation_options :
    option.domain_name => option
  }

  zone_id = var.hosted_zone_id
  name    = each.value.resource_record_name
  type    = each.value.resource_record_type
  records = [each.value.resource_record_value]

  # 검증용 레코드다. 짧게 둬서 처음 발급과 재발급이 빠르게 돌게 한다.
  ttl = 60

  # ★ 왜 덮어쓰기를 허용하는가 ★
  # 같은 이름의 검증 레코드가 이미 있을 수 있다. 인증서를 재생성하면 ACM 이
  # **같은 CNAME 이름을 다시 요구**하는데(도메인이 같으니까), 그때 기존
  # 레코드가 남아 있으면 Terraform 이 `already exists` 로 멈춘다.
  # 덮어써도 안전한 이유는 **이 레코드의 유일한 용도가 ACM 검증**이라서다.
  allow_overwrite = true
}

# 발급이 끝날 때까지 기다린다. 이 자원이 없으면 아직 `Pending validation` 인
# 인증서를 ALB 리스너에 붙이려 하다가 깨진다.
resource "aws_acm_certificate_validation" "main" {
  certificate_arn         = aws_acm_certificate.main.arn
  validation_record_fqdns = [for record in aws_route53_record.cert_validation : record.fqdn]

  # ★ 기본값은 75분이다. 30분으로 줄인다 ★
  # 같은 계정 Route 53 + DNS 검증이면 보통 몇 분이고, AWS 문서가 적는 상한이
  # 30분이다. 75분을 기다리는 것은 **고장을 늦게 아는 것**일 뿐이다.
  #
  # 여기서 시간이 초과되면 거의 항상 아래 셋 중 하나다:
  #   1. 갓 등록한 도메인이라 레지스트리에 NS 가 아직 안 퍼졌다
  #   2. 호스팅 존이 둘이고 레코드를 **레지스트리가 안 보는 쪽**에 넣었다
  #   3. hosted_zone_id 가 다른 도메인의 존이다
  # 1번이면 `terraform apply` 를 다시 돌리면 이어서 기다린다. 2·3번은
  # 기다려도 안 된다 — `terraform output` 과 콘솔의 NS 를 대조한다.
  #
  # ★ ACM 자체의 72시간은 **타임아웃**이다. 그때까지 검증되지 않으면 인증서가
  #   `Validation timed out` 이 되고 되살릴 수 없어 새로 요청해야 한다.
  #   즉 30분을 넘기면 "더 기다린다"가 아니라 "설정을 의심한다"가 맞다.
  timeouts {
    create = "30m"
  }
}

############################################################
# 서비스 레코드 — 도메인을 ALB 로 보낸다
############################################################

resource "aws_route53_record" "apex" {
  zone_id = var.hosted_zone_id
  name    = var.domain_name
  type    = "A"

  # ★ CNAME 이 아니라 alias 다. 두 가지 이유가 있다 ★
  # 1. DNS 규격상 **아펙스(루트 도메인)에는 CNAME 을 둘 수 없다.** 같은 이름에
  #    SOA·NS 가 이미 있어서다. alias 는 Route 53 확장이라 가능하다.
  # 2. alias 조회는 **과금되지 않는다.** CNAME 이면 조회마다 요금이 붙는다.
  alias {
    name    = aws_lb.main.dns_name
    zone_id = aws_lb.main.zone_id

    # ★ ALB 의 타깃이 전부 비정상이면 이 레코드를 응답에서 뺀다.
    #   지금은 ALB 가 하나라 **뺄 대안이 없어서** 실질 효과는
    #   "503 대신 NXDOMAIN" 뿐이다. 그래도 켜두는 이유는, 나중에
    #   가중치·장애조치 레코드를 붙일 때 이 플래그가 꺼져 있으면
    #   **고장난 쪽으로도 트래픽이 계속 간다**는 것을 잊기 쉽기 때문이다.
    evaluate_target_health = true
  }
}

# ★ AAAA(IPv6) 레코드는 없다. ALB 의 `ip_address_type` 이 `ipv4` 이므로
#   IPv6 주소가 없고, 레코드만 만들면 **IPv6 로 오는 클라이언트가 연결에
#   실패한다.** 둘은 같이 바꿔야 한다 (alb.tf 참조).

############################################################
# 출력
############################################################

output "certificate_arn" {
  description = "ALB 리스너에 붙이는 인증서. `validation` 쪽을 참조해 발급 완료를 보장한다."
  value       = aws_acm_certificate_validation.main.certificate_arn
}

output "service_url" {
  description = "브라우저에서 열 주소."
  value       = "https://${var.domain_name}"
}
