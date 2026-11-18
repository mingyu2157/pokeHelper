############################################################
# 컨테이너 레지스트리 (ADR-020 세부사항 6)
#
# 이미지는 **ARM(linux/arm64)** 으로 빌드한다. Fargate 에서 x86 대비 약 20%
# 싸다 (서울 기준 vCPU $0.03725 vs $0.04656).
############################################################

locals {
  repositories = toset(["backend", "frontend"])
}

resource "aws_ecr_repository" "app" {
  for_each = local.repositories

  name = "${var.project}/${each.key}"

  # ★ 같은 태그를 덮어쓸 수 없게 한다.
  #
  # `:latest` 를 계속 덮어쓰면 **지금 운영에서 도는 이미지가 무엇인지 알 수
  # 없다.** 롤백도 불가능하다 — 되돌릴 대상이 이미 사라졌기 때문이다.
  # 그래서 태그는 git 커밋 SHA 로 붙이고, ECS 태스크 정의가 그 SHA 를
  # 참조한다. 배포할 때마다 태스크 정의 리비전이 하나 늘어나는 것이 정상이다.
  image_tag_mutability = "IMMUTABLE"

  image_scanning_configuration {
    # 기본 스캔은 무료다. 끌 이유가 없다.
    scan_on_push = true
  }

  # ★ `destroy` 가 실제로 되게 한다.
  #
  # 기본값(false)이면 이미지가 하나라도 있을 때 `terraform destroy` 가
  # "repository contains images" 로 실패한다. 그러면 IaC 왕복(destroy → apply)
  # 이 성립하지 않는다. 이미지는 git 에서 언제든 다시 빌드되므로 지워도 된다.
  force_delete = true

  tags = { Name = "${var.project}-${each.key}" }
}

############################################################
# 수명주기 — 쌓이는 것을 막는다
#
# ECR 은 $0.10/GB·월이다. 백엔드·프론트 이미지가 각각 수백 MB 라 수십 개가
# 쌓이면 눈에 띄는 금액이 된다. **지우는 규칙이 없으면 무한히 쌓인다.**
############################################################

resource "aws_ecr_lifecycle_policy" "app" {
  for_each = aws_ecr_repository.app

  repository = each.value.name

  policy = jsonencode({
    rules = [
      {
        # 태그 없는 이미지 = 새 빌드가 밀어낸 이전 레이어. 바로 지운다.
        rulePriority = 1
        description  = "태그 없는 이미지는 하루 뒤 삭제"
        selection = {
          tagStatus   = "untagged"
          countType   = "sinceImagePushed"
          countUnit   = "days"
          countNumber = 1
        }
        action = { type = "expire" }
      },
      {
        # ★ 10개를 남기는 것은 "롤백할 수 있는 범위"다. 너무 적으면 어제 것도
        #   없고, 너무 많으면 돈이다.
        rulePriority = 2
        description  = "최근 10개만 보관"
        selection = {
          tagStatus   = "any"
          countType   = "imageCountMoreThan"
          countNumber = 10
        }
        action = { type = "expire" }
      },
    ]
  })
}

output "ecr_repository_urls" {
  description = "docker push 대상. 태그는 git 커밋 SHA 를 쓴다."
  value       = { for key, repo in aws_ecr_repository.app : key => repo.repository_url }
}
