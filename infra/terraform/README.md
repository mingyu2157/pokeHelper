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

## 1. 자격증명 준비 (손으로)

Terraform 이 AWS API 를 호출하려면 자격증명이 있어야 하는데, **그 자격증명은
Terraform 으로 만들 수 없다.** 그래서 이 단계만 콘솔에서 손으로 한다.
선택과 그 이유는 [ADR-021](../../docs/adr/021-aws-access-and-least-privilege.md).

| # | 누가 | 무엇을 |
|---|---|---|
| 1 | 루트 | MFA 등록, 루트 액세스 키 삭제 |
| 2 | 루트 | Billing 콘솔의 `Activate IAM access` — 안 하면 비용을 못 본다 |
| 3 | 루트 | IAM Identity Center 활성화 — **조직 인스턴스 / 단일 리전 / `ap-northeast-2`** |
| 4 | 루트 | 권한 세트 `PokehelperAdmin` 생성 → 사용자 만들고 **계정에 할당** |
| 5 | SSO 사용자 | MFA 등록 → `aws configure sso` |

★ **되돌릴 수 없는 것이 둘 있다.** ① Identity Center 를 켜면 AWS Organizations 가
생성되고, **프리 티어 크레딧 계정이면 크레딧이 즉시 소멸한다.** ② 인스턴스의
**주 리전은 생성 후 변경할 수 없다**(삭제 후 재생성뿐이고, 그때 권한 세트·할당·
사용자가 전부 사라진다). 둘 다 활성화 **직전에** 확인한다.

★ **계정 인스턴스(account instance)를 고르면 안 된다.** 단일 계정이라 자연스러워
보이지만, 계정 인스턴스는 **AWS 계정 접근 권한(permission set 할당)을 지원하지
않는다** — 콘솔·CLI 접근을 SSO 로 관리할 수 없게 된다.

> 단계별 화면과 입력값을 적은 `ACCESS.md` 는 포털 URL·계정 ID 같은 본인 환경
> 값이 들어가 **저장소에 커밋하지 않는다**(`.gitignore`). 위 표가 저장소에
> 남기는 전부다.

```bash
aws sso login --profile pokehelper
export AWS_PROFILE=pokehelper     # PowerShell: $env:AWS_PROFILE = "pokehelper"
aws sts get-caller-identity       # Arn 이 assumed-role/... 이면 된다
```

---

## 2. state 저장소 만들기 (최초 1회)

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

## 3. 본체 초기화

```bash
cd ..
cp backend.hcl.example backend.hcl         # 2단계 output 을 붙여넣는다

terraform init "-backend-config=backend.hcl"
terraform plan
```

> `terraform.tfvars` 는 **지금은 없어도 된다.** `domain_name`·`alarm_email` 을
> 쓰는 자원(`dns.tf`·`alb.tf`·`observability.tf`)이 아직 없다. 그것들을 추가할
> 때 `terraform.tfvars.example` 을 복사해 채운다.

★ **따옴표가 중요하다 — Windows PowerShell 5.1 한정 함정이다.**

```powershell
terraform init -backend-config=backend.hcl       # ❌ No positional arguments are expected
terraform init "-backend-config=backend.hcl"     # ✅
terraform init -backend-config backend.hcl       # ✅ (공백으로 분리해도 된다)
```

PowerShell 5.1 은 **`-` 로 시작하면서 `=` 가 들어간 인자**를 외부 프로그램에
넘길 때 쪼갠다. 그러면 terraform 이 `backend.hcl` 을 위치 인자로 받고 거부한다.
**terraform 의 문제가 아니라 셸의 문제**라, Git Bash·cmd·PowerShell 7 에서는
따옴표 없이도 된다. (`-var name=value` 처럼 **값 쪽에만 `=` 가 있으면** 괜찮다
— 그래서 bootstrap 의 `apply -var state_bucket_name=...` 는 그냥 됐다.)

`backend` 블록이 비어 있는 것은 의도다. 버킷 이름에 계정 ID 가 들어가 커밋할
수 없고, `backend` 블록은 변수를 못 쓰기 때문이다.

---

## 4. 적용 순서

한 번에 전부 올리지 않는다. 깨지면 **무엇 때문에 깨졌는지** 알 수 없다.

| 단계 | 대상 | 확인할 것 |
|---|---|---|
| 1 | `network.tf`, `security_groups.tf` | 서브넷 4개, 보안그룹 4개 |
| 2 | `ecr.tf` + ARM 이미지 push | `infra/scripts/push-images.ps1` (아래 5-B) |
| 3 | `data.tf` (RDS·ElastiCache) | ★ **코드는 있고 apply 는 5단계와 함께** (아래) |
| 4 | `iam.tf`, `secrets.tf` | 역할 3개 · SSM 값을 손으로 넣었는가 |
| 5 | `dns.tf`, `alb.tf`, `ecs.tf` | `/health` 가 200 인가 |
| 6 | `scheduler.tf`, `observability.tf` | 다음 날 수집이 돌았는가 |

```bash
terraform apply -target=aws_vpc.main -target=aws_subnet.public ...
```

> `-target` 은 평소에 쓰면 안 되는 옵션이다. **최초 구축 때만** 쓰고, 그 뒤엔
> 항상 전체 `apply` 로 돌린다. 계속 쓰면 state 와 실제가 조용히 어긋난다.

★ **3단계(`data.tf`)의 apply 를 5단계까지 미룬다.** RDS·ElastiCache 는 만든
순간부터 시간당 과금인데(월 ~$36) 접속할 태스크가 아직 없다. 빈 DB 를 켜두고
하루 $1.2 를 내는 것은 아무것도 사지 않는 지출이다. 그래서 코드·테스트·`plan`
까지만 먼저 하고, 실제 생성은 ALB·ECS 와 같이 한다.

반대 의견도 있었다 — RDS 는 생성에 10분 이상 걸리고 서브넷 그룹·파라미터
그룹에서 처음 깨지기 쉬우니, 미루면 **ECS 작업 중에 두 가지가 동시에 깨진다.**
그건 "그날 안에 ECS 까지 간다"는 전제가 있을 때만 성립해서 미루는 쪽을 골랐다.
(2026-11-23)

★ **반대로 4단계(`iam.tf`)는 미루지 않는다.** IAM 역할은 **무료**고, 태스크
정의가 역할 ARN 을 참조하므로 **ECS 보다 먼저 있어야 한다.** `iam.tf` 는 아직
없는 `ecs.tf` 의 자원을 참조하지 않고 이름 규칙으로 ARN 을 만들기 때문에 단독
apply 가 된다 — 대신 **`ecs.tf` 의 클러스터 이름이 `local.name` 이어야 한다.**
어긋나면 스케줄러 정책의 `ecs:cluster` 조건이 안 맞아 **RunTask 만 조용히
막힌다.**

### 실행 역할과 태스크 역할 (ADR-021 세부사항 6)

ECS 에서 가장 자주 틀리는 구분이라 여기에도 적어둔다.

| | 누가 쓰나 | 언제 | 무엇을 |
|---|---|---|---|
| **실행** 역할 | ECS 에이전트 (컨테이너 **밖**) | 태스크가 **뜨기 전** | ECR pull · 로그 스트림 생성 · SSM 값을 환경변수로 주입 |
| **태스크** 역할 | 컨테이너 **안**의 앱 코드 | 도는 동안 | 앱이 호출하는 AWS API (우리는 ECS Exec 만) |

★ "SSM 파라미터를 못 읽는다"는 거의 항상 **실행 역할** 문제다. 이름만 보고
태스크 역할을 고치면 같은 실패가 반복된다.
(2026-11-23)

---

## 5-B. 이미지 빌드·푸시

★ **이 문서의 다른 절과 달리 저장소 루트에서 실행한다.** 2~3절이 이 디렉터리
기준이라, 거기서 이어서 치면 `CommandNotFoundException` 이 난다.

```powershell
cd ..\..                                     # ← 저장소 루트. 빼먹으면 스크립트를 못 찾는다
aws sso login --profile pokehelper
.\infra\scripts\push-images.ps1 -DryRun     # 무엇을 할지만 본다
.\infra\scripts\push-images.ps1             # 실제로 빌드·푸시
```

경로를 틀려도 **빌드가 잘못되지는 않는다** — 스크립트가 `$PSScriptRoot` 로 저장소
루트를 직접 찾아 `Set-Location` 한다. 깨지는 건 "스크립트를 찾는 것"뿐이다.
그 `Set-Location` 은 세션 전체에 걸리므로, **끝나면 저장소 루트에 서 있다.**

스크립트가 대신 지켜주는 것 셋:

| | 왜 |
|---|---|
| 태그 = **git 커밋 SHA** | ECR 이 `IMMUTABLE` 이라 `:latest` 를 두 번 못 민다. 그리고 latest 를 덮어쓰면 **무엇이 떠 있는지 모르고 롤백 대상도 사라진다** |
| 플랫폼 = **linux/arm64** | 빼먹으면 x86 이 올라가고 **ECS 가 태스크를 못 띄울 때까지 모른다**. 빌드 후 `Architecture` 를 단정한다 |
| 작업 트리가 더러우면 **거부** | SHA 가 이미지를 설명하지 않게 된다. `-AllowDirty` 로만 통과 |

빌드는 QEMU 에뮬레이션이라 **백엔드 약 3분, 프론트 약 8분**이다.
자격증명 확인을 **빌드 전에** 한다 — 끝난 뒤 push 에서 만료되면 그 시간이 날아간다.

### 최초 1회: ECR 자격증명 헬퍼 설정

이 스크립트는 **`docker login` 을 쓰지 않는다.** 대신 `~/.docker/config.json` 에
레지스트리별 헬퍼를 등록해 둔다. 한 번만 하면 된다.

```powershell
$acct = aws sts get-caller-identity --profile pokehelper --query Account --output text
$reg  = "$acct.dkr.ecr.ap-northeast-2.amazonaws.com"
$path = "$env:USERPROFILE\.docker\config.json"

Copy-Item $path "$path.bak" -Force
$c = Get-Content $path -Raw | ConvertFrom-Json
if (-not $c.credHelpers) { $c | Add-Member credHelpers ([pscustomobject]@{}) }
$c.credHelpers | Add-Member -Name $reg -Value "ecr-login" -MemberType NoteProperty -Force
[IO.File]::WriteAllText($path, ($c | ConvertTo-Json -Depth 20), (New-Object System.Text.UTF8Encoding $false))
```

`docker-credential-ecr-login` 은 **Docker Desktop 이 함께 설치한다.** 따로 받을
필요 없다. 설정이 없으면 스크립트가 무엇을 넣어야 하는지 출력하고 멈춘다.

★ **`Set-Content -Encoding utf8` 을 쓰지 마라.** PS 5.1 은 BOM 을 붙이고, docker 의
JSON 파서는 BOM 이 있으면 config.json 을 아예 읽지 못한다.

### ★ 왜 `docker login` 을 쓰지 않는가

Windows 에서 **두 벽에 연달아 막혔다** (JOURNAL 2026-11-23). 둘 다 `docker login`
경로에만 있는 문제다.

| 증상 | 원인 |
|---|---|
| `400 Bad Request` | 코드 페이지 65001 에서 PS 5.1 이 외부 프로그램 stdin 에 UTF-8 BOM 을 먼저 쓴다. `--password-stdin` 은 **뒤쪽 개행만** 떼고 앞쪽 BOM 은 떼지 않아 비밀번호가 깨진 채 간다. 401 이 아니라 **400** 인 것이 "권한이 아니라 바이트"라는 단서였다. BOM 의 출처는 `$OutputEncoding` 이 **아니라** `[Console]::InputEncoding` 이다 |
| `The stub received bad data` | ①을 우회해도 다음 벽. **Windows 자격증명 관리자는 비밀 하나를 약 2.5KB 까지만** 저장한다. ECR 토큰은 3000자대라 넘쳐서 `desktop`·`wincred` 헬퍼가 깨진다 |

헬퍼는 **토큰을 저장하지 않는다.** push 때마다 AWS 자격증명으로 새로 받으므로
두 문제가 같이 사라지고, 12시간 만료도 신경 쓸 필요가 없다.

---

## 5. 검사

```bash
terraform fmt -check -recursive
terraform validate

# ★ 보안그룹 규칙 — 퍼블릭 서브넷을 골랐으므로 이게 유일한 방어선이다
docker compose exec backend uv run pytest tests/integration/test_terraform_security_groups.py

# ★ IAM — 넓어지는 방향을 막는다. 특히 iam:PassRole
docker compose exec backend uv run pytest tests/integration/test_terraform_iam.py

# 데이터 저장소의 위험한 기본값 · 예산
docker compose exec backend uv run pytest tests/integration/test_terraform_data.py \
                                          tests/integration/test_terraform_budget.py
```

보안그룹 테스트는 **`.tf` 소스를 읽는다. 실제 AWS 계정을 보지 않는다.**
`terraform plan` 은 자격증명이 필요해 CI 에서 돌릴 수 없기 때문이다. 따라서
**콘솔에서 손으로 고친 규칙은 다음 `apply` 전까지 잡히지 않는다.**

---

## 6. 내릴 때

★ **`destroy` 한 번으로는 안 내려간다.** RDS 에 `deletion_protection = true` 가
걸려 있어 거부된다. 번거로운 것이 목적이다 — 운영 DB 가 실수로 사라지면 안 된다.

```bash
# 1단계: data.tf 에서 그 줄을 직접 고치고 apply 한다
#   resource "aws_db_instance" "main" { ... deletion_protection = false ... }
terraform apply

# 2단계
terraform destroy
```

★ **변수로 빼지 않은 것이 의도다.** `-var` 한 줄로 끌 수 있으면 보호의 의미가
줄어든다. 코드를 고쳐야 하므로 **diff 에 남고, 리뷰에 걸리고, 되돌리기 쉽다.**
(그래서 `test_terraform_data.py` 는 이 값이 변수가 아니라 `true` 인지를 본다.)

지워지지 **않는** 것:

| 자원 | 이유 |
|---|---|
| state 버킷 · 잠금 테이블 | `prevent_destroy`. 본체와 생애주기가 다르다 |
| 도메인 등록 | Terraform 이 관리하지 않는다 |
| RDS 최종 스냅샷 | `skip_final_snapshot = false` 면 남는다. **남으면 과금된다** |
| ~~ECR 이미지~~ | `force_delete = true` 라 **지워진다**. git 에서 다시 빌드하면 된다 |
| CloudWatch 로그 | 보존 기간이 지나야 사라진다 |

`destroy` 후 **콘솔에서 RDS 스냅샷을 직접 확인**한다. "지웠는데
요금이 나온다"의 대부분이 여기서 나온다.

---

## 7. 비용

ADR-020 기준 **월 약 $98** (프리 티어 없음 가정, 730시간).
가장 큰 항목은 ALB($22) · RDS($18) · ElastiCache($18) · 태스크($28) 다.

여기에 **Secrets Manager 시크릿 1개 월 $0.40** 이 더해진다. RDS 마스터
비밀번호를 Terraform 이 들고 있지 않게 하려고(`manage_master_user_password`)
낸 비용이다 — 직접 만들면 **값이 state 에 평문으로 들어간다.**

청구 알람이 `observability.tf` 에 들어 있다. 임계치는
`monthly_budget_usd`(기본 150)다. **비용 절감 장치가 아니라 사고 방지
장치**다 — 로그 폭증이나 데이터 전송 증가는 조용히 일어난다.
