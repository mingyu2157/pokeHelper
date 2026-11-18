############################################################
# state 저장소를 만드는 Terraform (ADR-020)
#
# ★ 닭-달걀 문제 ★
#
# 본체(`../`)는 state 를 S3 에 둔다. 그런데 그 S3 버킷을 Terraform 으로
# 만들려면 그 Terraform 의 state 를 어딘가 둬야 한다. 그래서 이 디렉터리만
# **로컬 state** 로 돌린다. 한 번 만들고 거의 건드리지 않는다.
#
# 여기서 만든 state 파일(`terraform.tfstate`)은 .gitignore 로 커밋되지 않는다.
# 잃어버려도 치명적이지 않다 — 버킷과 테이블은 이미 존재하고, 본체는 그것을
# 이름으로만 참조한다. 다시 만들려면 `terraform import` 하거나, 이 디렉터리를
# 쓰지 않고 손으로 둬도 된다.
############################################################

terraform {
  required_version = ">= 1.9"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.80"
    }
  }
  # ★ 백엔드 선언이 없다. 의도적으로 로컬 state 다.
}

provider "aws" {
  region = var.region

  default_tags {
    tags = {
      Project   = "pokehelper"
      ManagedBy = "terraform"
      Component = "tfstate-backend"
    }
  }
}

variable "region" {
  description = "state 저장소를 둘 리전. 본체와 같은 리전에 둔다."
  type        = string
  default     = "ap-northeast-2"
}

variable "state_bucket_name" {
  description = <<-EOT
    state 를 담을 S3 버킷 이름. S3 버킷 이름은 **전 세계에서 유일**해야 하므로
    계정 ID 같은 접미사를 붙인다. 예: pokehelper-tfstate-123456789012
  EOT
  type        = string

  validation {
    condition     = can(regex("^[a-z0-9][a-z0-9.-]{2,62}$", var.state_bucket_name))
    error_message = "S3 버킷 이름 규칙을 지켜야 한다 (소문자·숫자·하이픈, 3~63자)."
  }
}

############################################################
# state 버킷
############################################################

resource "aws_s3_bucket" "tfstate" {
  bucket = var.state_bucket_name

  # ★ 실수로 지우는 것을 막는다. state 를 잃으면 Terraform 은 자기가 만든
  #   자원을 모르게 되고, 전부 손으로 찾아 지워야 한다.
  lifecycle {
    prevent_destroy = true
  }
}

# 버전 관리 — state 가 깨졌을 때 직전 버전으로 되돌릴 수 있는 유일한 방법이다.
resource "aws_s3_bucket_versioning" "tfstate" {
  bucket = aws_s3_bucket.tfstate.id

  versioning_configuration {
    status = "Enabled"
  }
}

# state 에는 DB 비밀번호·엔드포인트가 평문으로 들어간다. 암호화는 선택이 아니다.
resource "aws_s3_bucket_server_side_encryption_configuration" "tfstate" {
  bucket = aws_s3_bucket.tfstate.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

# 같은 이유로 공개 접근을 전부 막는다.
resource "aws_s3_bucket_public_access_block" "tfstate" {
  bucket = aws_s3_bucket.tfstate.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

############################################################
# 잠금 테이블
#
# 두 사람이(또는 CI 와 사람이) 동시에 apply 하면 state 가 꼬인다.
# 혼자 쓰더라도, 앞 apply 가 끝나기 전에 또 돌리는 실수를 막아준다.
#
# ※ Terraform 1.10 부터 S3 백엔드가 `use_lockfile = true` 로 자체 잠금을
#   지원해서 이 테이블 없이도 된다. 지금 DynamoDB 를 쓰는 이유는 더 널리
#   쓰이는 구성이고 비용이 사실상 0(온디맨드, 쓸 때만 과금)이기 때문이다.
#   Terraform 을 1.10 이상으로 고정하게 되면 이 자원을 지우고 백엔드 설정만
#   바꾸면 된다.
############################################################

resource "aws_dynamodb_table" "tflock" {
  name         = "${var.state_bucket_name}-lock"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "LockID"

  attribute {
    name = "LockID"
    type = "S"
  }

  lifecycle {
    prevent_destroy = true
  }
}

############################################################
# 본체의 backend.hcl 에 그대로 넣을 값
############################################################

output "backend_config" {
  description = "본체 디렉터리의 backend.hcl 에 복사한다."
  value       = <<-EOT
    bucket         = "${aws_s3_bucket.tfstate.id}"
    key            = "pokehelper/prod/terraform.tfstate"
    region         = "${var.region}"
    dynamodb_table = "${aws_dynamodb_table.tflock.name}"
    encrypt        = true
  EOT
}
