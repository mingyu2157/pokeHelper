variable "region" {
  description = "배포 리전. ADR-020 의 비용 조사가 전부 이 리전 기준이다."
  type        = string
  default     = "ap-northeast-2"
}

variable "project" {
  description = "자원 이름과 태그의 접두사."
  type        = string
  default     = "pokehelper"
}

variable "environment" {
  description = "환경명. 자원 이름에 들어간다."
  type        = string
  default     = "prod"

  validation {
    condition     = contains(["prod", "staging"], var.environment)
    error_message = "prod 또는 staging 만 허용한다."
  }
}

variable "vpc_cidr" {
  description = "VPC 대역."
  type        = string
  default     = "10.0.0.0/16"
}

variable "domain_name" {
  description = <<-EOT
    서비스 도메인 (예: pokehelper.example). ACM 인증서를 이 이름으로 발급하고
    ALB 에 붙인다.

    ★ 도메인 **등록 자체는 Terraform 으로 하지 않는다.** 등록은 1년 약정이고
    `terraform destroy` 로 되돌릴 수 없으며, 등록자 연락처(개인정보)를 넣어야
    한다. 콘솔에서 등록한 뒤 호스팅 존 ID 를 여기에 넘긴다.

    ★ 지금은 비어 있어도 된다 — 이 값을 쓰는 `dns.tf`·`alb.tf` 가 아직 없다.
    그것들을 추가할 때 아래 validation 이 빈 값을 막는다. **쓰지도 않는 값을
    필수로 두면, 네트워크만 올리려는 사람이 도메인부터 사야 한다.**
  EOT
  type        = string
  default     = ""
}

variable "hosted_zone_id" {
  description = "도메인 등록 시 Route53 이 만든 퍼블릭 호스팅 존 ID. 지금은 비어 있어도 된다."
  type        = string
  default     = ""

  validation {
    # 둘 중 하나만 채우면 dns.tf 가 반쯤 설정된 상태로 돌아간다. 같이 채운다.
    condition     = (var.hosted_zone_id == "") == (var.domain_name == "")
    error_message = "domain_name 과 hosted_zone_id 는 **둘 다** 채우거나 둘 다 비워야 한다."
  }
}

############################################################
# 태스크 크기 — ADR-020 의 비용표와 직접 연결된다.
# 바꾸면 월 청구액이 바뀐다.
############################################################

variable "backend_cpu" {
  description = "백엔드 태스크 vCPU (1024 = 1 vCPU). 사이드카 2개를 포함한 값이다."
  type        = number
  default     = 512
}

variable "backend_memory" {
  description = "백엔드 태스크 메모리(MiB). prometheus·grafana 사이드카 몫이 들어 있다."
  type        = number
  default     = 2048
}

variable "frontend_cpu" {
  description = "프론트엔드 태스크 vCPU."
  type        = number
  default     = 256
}

variable "frontend_memory" {
  description = "프론트엔드 태스크 메모리(MiB)."
  type        = number
  default     = 512
}

variable "db_instance_class" {
  description = "RDS 인스턴스 클래스. ADR-020 은 db.t4g.micro Single-AZ 를 전제한다."
  type        = string
  default     = "db.t4g.micro"
}

variable "cache_node_type" {
  description = "ElastiCache 노드 타입."
  type        = string
  default     = "cache.t4g.micro"
}

variable "db_engine_version" {
  description = <<-EOT
    PostgreSQL 버전. **패치 버전을 적지 않는다** — `"16"` 으로 주면 AWS 가 그
    시점의 최신 마이너를 고른다.

    ★ 패치 버전을 박으면 그 버전이 내려간 시점·리전에서 **apply 가 죽는다.**
    그리고 확인하지 않은 버전 문자열을 추측해 넣는 것은 CLAUDE.md 가 금지한다.
  EOT
  type        = string
  default     = "16"
}

variable "db_name" {
  description = "초기 데이터베이스 이름. 로컬 compose 의 POSTGRES_DB 와 같게 둔다."
  type        = string
  default     = "champs"
}

variable "db_master_username" {
  description = <<-EOT
    RDS 마스터 사용자명.

    ★ 로컬의 `champs_master` 와 **같은 이름**이다. 로컬 initdb 는 슈퍼유저로
    champs_master 를 만든 뒤 그 계정으로 roles.sql 을 돌리는데, RDS 에서는
    마스터 사용자가 그 자리를 그대로 맡는다. 이름을 맞춰두면 **같은 roles.sql
    이 양쪽에서 동작한다** (ADR-015).
  EOT
  type        = string
  default     = "champs_master"

  validation {
    # RDS 가 거부하는 이름을 apply 전에 막는다. 예약어는 더 많지만 흔한 것만.
    condition     = !contains(["admin", "rdsadmin", "postgres", "root"], lower(var.db_master_username))
    error_message = "RDS 가 예약한 이름이다. 다른 이름을 쓴다."
  }
}

variable "db_allocated_storage" {
  description = "RDS 초기 스토리지(GB). gp3 최소가 20 이다."
  type        = number
  default     = 20

  validation {
    condition     = var.db_allocated_storage >= 20
    error_message = "gp3 는 최소 20GB 다."
  }
}

variable "db_max_allocated_storage" {
  description = <<-EOT
    스토리지 자동 확장 상한(GB).

    ★ 디스크가 꽉 차면 DB 가 멈추는데, 그건 알림이 아니라 **장애로** 알게 된다.
    상한을 둬서 멈추는 대신 늘어나게 한다. 늘어난 만큼만 과금되므로 평소 비용은
    그대로다. 초기값보다 커야 자동 확장이 켜진다.
  EOT
  type        = number
  default     = 100
}

variable "db_backup_retention_days" {
  description = <<-EOT
    자동 백업 보존 일수.

    ★ 0 이면 자동 백업이 **꺼지고, 그러면 PITR 도 불가능하다.** ADR-020 의
    RPO 5분은 자동 백업이 켜져 있다는 전제에 기대고 있다. 1 이상이어야 한다.
  EOT
  type        = number
  default     = 7

  validation {
    condition     = var.db_backup_retention_days >= 1
    error_message = "1 이상이어야 한다. 0 은 자동 백업을 끄고 PITR 을 불가능하게 만든다 (ADR-020 결정 3)."
  }
}

variable "cache_engine_version" {
  description = "Redis 버전. parameter_group_name 의 default.redis7 과 맞춰야 한다."
  type        = string
  default     = "7.1"
}

variable "monthly_budget_usd" {
  description = <<-EOT
    청구 알람 임계치(USD). ADR-020 의 예상 월 $98 의 1.5배를 기본값으로 둔다.

    ★ 비용 절감 장치가 아니라 **사고 방지 장치**다. 로그 폭증이나 데이터 전송
    증가는 조용히 일어나고, 알람이 없으면 월말 청구서로 알게 된다.
  EOT
  type        = number
  default     = 150
}

variable "alarm_email" {
  description = <<-EOT
    청구 알람을 받을 주소. apply 후 SNS 구독 확인 메일이 한 번 온다.

    ★ **필수값이다.** `observability.tf` 가 이 주소로 예산 알림을 보낸다.
    기본값을 주면 **주소를 안 넣은 채로 예산이 만들어지고, 아무에게도 알림이
    가지 않는 상태**가 된다 — 그건 알림이 없는 것보다 나쁘다. "알림을 달아뒀다"고
    믿게 되기 때문이다.

    (dns.tf 가 없던 시절의 domain_name 처럼 한동안 선택값이었다. 쓰는 자원이
     생겼으므로 되돌린다.)
  EOT
  type        = string

  validation {
    # 점은 [.] 로 쓴다 — HCL 큰따옴표 문자열에서 `\.` 는 유효한 이스케이프가 아니다.
    condition     = can(regex("^[^@[:space:]]+@[^@[:space:]]+[.][^@[:space:]]+$", var.alarm_email))
    error_message = "alarm_email 은 이메일 주소여야 한다. 오타가 나면 알림이 조용히 사라진다."
  }
}
