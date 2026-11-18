# infra/terraform

pokeHelper 운영 인프라. 구성과 그렇게 고른 이유는
[ADR-019](../../docs/adr/019-deployment-target-and-prerequisites.md)·
[ADR-020](../../docs/adr/020-production-topology.md) 에 있다.

대상 리전은 **서울(`ap-northeast-2`)** 이다. ADR-020 의 비용 조사가 전부 이
리전 기준이라, 리전을 바꾸면 비용표가 무효가 된다.

---

## 0. 먼저 알아둘 것

- **도메인 등록은 Terraform 으로 하지 않는다.** 1년 약정이고 `destroy` 로
  되돌릴 수 없으며, 등록자 연락처(개인정보)를 넣어야 한다. 콘솔에서 등록한 뒤
  **호스팅 존 ID** 를 `terraform.tfvars` 에 넘긴다.
- **시크릿 값은 Terraform 에 넣지 않는다.** SSM 파라미터는 Terraform 이
  **만들기만** 하고, 값은 `aws ssm put-parameter` 로 따로 넣는다. 값을 코드에
  적으면 state 파일과 plan 출력에 평문으로 남는다.
- `terraform.tfvars`, `backend.hcl`, `*.tfstate`, `.terraform/` 은 커밋하지
  않는다. `.terraform.lock.hcl` 은 **커밋한다** — 프로바이더 버전을 고정한다.

---

## 1. state 저장소 만들기 (최초 1회)

```bash
cd bootstrap
terraform init                       # 로컬 state 다. 백엔드 없음
terraform apply -var state_bucket_name=pokehelper-tfstate-<계정ID>
terraform output backend_config      # 아래에서 쓴다
```

> **왜 디렉터리가 따로인가** — 본체는 state 를 S3 에 두는데, 그 S3 버킷을
> Terraform 으로 만들려면 그 Terraform 의 state 를 어딘가 둬야 한다.
> 닭-달걀이라 이 디렉터리만 로컬 state 로 돈다.

---

## 2. 본체 초기화

```bash
cd ..
cp backend.hcl.example backend.hcl         # 1단계 output 을 붙여넣는다
cp terraform.tfvars.example terraform.tfvars

terraform init -backend-config=backend.hcl
terraform plan
```

`backend` 블록이 비어 있는 것은 의도다. 버킷 이름에 계정 ID 가 들어가 커밋할
수 없고, `backend` 블록은 변수를 못 쓰기 때문이다.

---

## 3. 적용 순서

한 번에 전부 올리지 않는다. 깨지면 **무엇 때문에 깨졌는지** 알 수 없다.

| 단계 | 대상 | 확인할 것 |
|---|---|---|
| 1 | `network.tf`, `security_groups.tf` | 서브넷 4개, 보안그룹 4개 |
| 2 | `ecr.tf` + ARM 이미지 push | `docker pull` 이 되는가 |
| 3 | `data.tf` (RDS·ElastiCache) | 태스크에서 접속되는가 |
| 4 | `iam.tf`, `secrets.tf` | SSM 값을 손으로 넣었는가 |
| 5 | `dns.tf`, `alb.tf`, `ecs.tf` | `/health` 가 200 인가 |
| 6 | `scheduler.tf`, `observability.tf` | 다음 날 수집이 돌았는가 |

```bash
terraform apply -target=aws_vpc.main -target=aws_subnet.public ...
```

> `-target` 은 평소에 쓰면 안 되는 옵션이다. **최초 구축 때만** 쓰고, 그 뒤엔
> 항상 전체 `apply` 로 돌린다. 계속 쓰면 state 와 실제가 조용히 어긋난다.

---

## 4. 검사

```bash
terraform fmt -check -recursive
terraform validate

# ★ 보안그룹 규칙 — 퍼블릭 서브넷을 골랐으므로 이게 유일한 방어선이다
docker compose exec backend uv run pytest tests/integration/test_terraform_security_groups.py
```

보안그룹 테스트는 **`.tf` 소스를 읽는다. 실제 AWS 계정을 보지 않는다.**
`terraform plan` 은 자격증명이 필요해 CI 에서 돌릴 수 없기 때문이다. 따라서
**콘솔에서 손으로 고친 규칙은 다음 `apply` 전까지 잡히지 않는다.**

---

## 5. 내릴 때

```bash
terraform destroy
```

지워지지 **않는** 것:

| 자원 | 이유 |
|---|---|
| state 버킷 · 잠금 테이블 | `prevent_destroy`. 본체와 생애주기가 다르다 |
| 도메인 등록 | Terraform 이 관리하지 않는다 |
| RDS 최종 스냅샷 | `skip_final_snapshot = false` 면 남는다. **남으면 과금된다** |
| ECR 이미지 | 리포지토리를 지우려면 `force_delete` 가 필요하다 |
| CloudWatch 로그 | 보존 기간이 지나야 사라진다 |

`destroy` 후 **콘솔에서 RDS 스냅샷과 ECR 이미지를 직접 확인**한다. "지웠는데
요금이 나온다"의 대부분이 여기서 나온다.

---

## 6. 비용

ADR-020 기준 **월 약 $98** (프리 티어 없음 가정, 730시간).
가장 큰 항목은 ALB($22) · RDS($18) · ElastiCache($18) · 태스크($28) 다.

청구 알람이 `observability.tf` 에 들어 있다. 임계치는
`monthly_budget_usd`(기본 150)다. **비용 절감 장치가 아니라 사고 방지
장치**다 — 로그 폭증이나 데이터 전송 증가는 조용히 일어난다.
