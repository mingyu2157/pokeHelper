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
    한다. 콘솔에서 등록한 뒤 호스팅 존 ID 를 여기에 넘긴다 (README 3장).

    ★ **필수값이 됐다 (2026-11-23).** 한동안 선택값이었다 — 이 값을 쓰는 자원이
    없었고, "쓰지도 않는 값을 필수로 두면 네트워크만 올리려는 사람이 도메인부터
    사야 한다"는 이유였다. `dns.tf`·`alb.tf` 가 생겼으므로 되돌린다.
    (`alarm_email` 이 `observability.tf` 가 생길 때 밟은 길과 같다.)
  EOT
  type        = string

  validation {
    # 빈 값이면 ACM 이 `ValidationException` 을 내는데, 그 메시지는 무엇이
    # 비었는지 말해주지 않는다. apply 전에 여기서 막는다.
    condition     = length(trimspace(var.domain_name)) > 0
    error_message = "domain_name 이 필요하다. 콘솔에서 도메인을 등록한 뒤 terraform.tfvars 에 넣는다 (README 3장)."
  }

  validation {
    # ★ 스킴·경로·끝 점을 걸러낸다. `https://x.com` 이나 `x.com/` 을 그대로
    #   넘기면 ACM 이 받아주지 않거나 **엉뚱한 이름으로 발급**된다.
    condition     = can(regex("^[a-z0-9]([a-z0-9-]*[a-z0-9])?([.][a-z0-9]([a-z0-9-]*[a-z0-9])?)+$", var.domain_name))
    error_message = "domain_name 은 호스트명만 적는다. 스킴(https://)·경로·끝 점 없이 소문자로."
  }
}

variable "hosted_zone_id" {
  description = <<-EOT
    도메인 등록 시 Route 53 이 **자동으로 만든** 퍼블릭 호스팅 존 ID.

    ★ 존을 Terraform 으로 새로 만들지 않는다. 같은 이름의 존이 둘이 되면
    레지스트리가 가리키는 NS 는 먼저 생긴 쪽이라, 나중에 만든 존에 넣은
    레코드는 **아무도 보지 못한다** (`dns.tf` 머리말).
  EOT
  type        = string

  validation {
    condition     = length(trimspace(var.hosted_zone_id)) > 0
    error_message = "hosted_zone_id 가 필요하다. Route 53 → Hosted zones 에서 복사한다 (README 3장)."
  }

  validation {
    # 존 ID 는 대문자·숫자 문자열이다 (`Z` 로 시작). 도메인 이름을 잘못 넣는
    # 실수가 흔한데, 그러면 `NoSuchHostedZone` 이 뜨고 원인이 안 보인다.
    condition     = can(regex("^Z[A-Z0-9]+$", var.hosted_zone_id))
    error_message = "hosted_zone_id 는 Z 로 시작하는 존 ID 다. 도메인 이름이 아니다."
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

variable "image_tag" {
  description = <<-EOT
    태스크 정의가 가리킬 이미지 태그. **git 커밋 SHA 12자**다
    (`infra/scripts/push-images.ps1` 이 `git rev-parse --short=12 HEAD` 로 붙인다).

    ★ **기본값을 주지 않는다.** `latest` 같은 기본값을 두면 ECR 이
    `IMMUTABLE` 이라 애초에 그런 태그가 없어서 apply 가 "이미지를 못 찾는다"로
    죽거나, 더 나쁘게는 **어떤 커밋이 떠 있는지 알 수 없는 상태**가 된다.
    무엇이 돌고 있는지를 git 에서 되짚을 수 있어야 한다.

    값 얻는 법:
      git rev-parse --short=12 HEAD
  EOT
  type        = string

  validation {
    # ★ `-dirty` 접미사를 일부러 거부한다. push-images.ps1 은 작업 트리가
    #   더러우면 태그에 `-dirty` 를 붙이는데, 그 이미지는 **어떤 커밋도
    #   설명하지 못한다.** 로컬 확인용으로는 쓸 수 있지만 운영에 올리면
    #   "이 커밋의 이미지"라는 전제가 깨진다.
    condition     = can(regex("^[0-9a-f]{12}$", var.image_tag))
    error_message = "image_tag 는 소문자 16진수 12자여야 한다 (git rev-parse --short=12 HEAD). -dirty 는 운영에 올리지 않는다."
  }
}

variable "log_retention_days" {
  description = <<-EOT
    ECS 로그 그룹 보존 기간(일).

    ★ **보존 기간을 반드시 명시한다.** 지정하지 않으면 CloudWatch 로그 그룹이
    "만료 없음"으로 생기고 로그가 영원히 쌓인다 — 저장 비용이 **조용히** 올라가고
    월말 청구서로 알게 된다 (ADR-020 세부사항 8과 같은 종류의 함정).

    `iam.tf` 가 실행 역할에서 `logs:CreateLogGroup` 을 뺀 이유가 이것이다.
    에이전트가 그룹을 직접 만들면 보존 기간 없이 생긴다. 그룹은 Terraform 이
    만들고, 그래서 이 값이 강제된다.
  EOT
  type        = number
  default     = 14

  validation {
    # CloudWatch 가 받는 값은 열거형이다. 아무 숫자나 주면 apply 가 거부한다.
    condition = contains(
      [1, 3, 5, 7, 14, 30, 60, 90, 120, 150, 180, 365, 400, 545, 731, 1096, 1827, 2192, 2557, 2922, 3288, 3653],
      var.log_retention_days
    )
    error_message = "log_retention_days 는 CloudWatch 가 허용하는 값이어야 한다 (1·3·5·7·14·30·60·90·120·150·180·365·400·545·731·…)."
  }
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
