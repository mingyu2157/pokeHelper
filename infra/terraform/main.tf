############################################################
# pokeHelper 운영 인프라 (ADR-019 / ADR-020)
#
# 적용 순서와 주의사항은 README.md 를 먼저 읽는다.
############################################################

terraform {
  required_version = ">= 1.9"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.80"
    }
  }

  # ★ 비어 있는 것이 의도다.
  #
  # 버킷 이름에 계정 ID 가 들어가서 저장소에 커밋하면 계정 정보가 노출되고,
  # 다른 계정에서 이 코드를 쓸 수 없게 된다. 값은 `backend.hcl` 로 넘긴다:
  #
  #   terraform init -backend-config=backend.hcl
  #
  # backend.hcl 은 .gitignore 대상이고, backend.hcl.example 이 모양을 보여준다.
  # backend 블록은 변수를 못 쓰므로 이 방법 말고는 하드코딩뿐이다.
  backend "s3" {}
}

provider "aws" {
  region = var.region

  # 모든 자원에 붙는다. 비용 탐색기에서 이 프로젝트만 걸러 보려면 필요하다.
  default_tags {
    tags = {
      Project     = var.project
      Environment = var.environment
      ManagedBy   = "terraform"
    }
  }
}

# ACM 인증서는 ALB 와 **같은 리전**에 있어야 한다. 별도 프로바이더가 필요한 것은
# CloudFront 를 쓸 때(us-east-1 고정)이고, 우리는 ALB 에 직접 붙이므로 하나면 된다.

data "aws_availability_zones" "available" {
  state = "available"
}

data "aws_caller_identity" "current" {}

locals {
  name = "${var.project}-${var.environment}"

  # ★ AZ 두 개를 쓴다. 단일 AZ 로 운영하기로 했는데도(ADR-020 결정 3) 서브넷을
  #   두 AZ 에 만드는 이유는 **ALB 와 RDS 서브넷 그룹이 최소 2개 AZ 를 요구**
  #   하기 때문이다. 서브넷이 두 AZ 에 있는 것과 인스턴스가 두 AZ 에 뜨는 것은
  #   다른 얘기다 — RDS 인스턴스는 Single-AZ 로 뜬다.
  azs = slice(data.aws_availability_zones.available.names, 0, 2)
}
