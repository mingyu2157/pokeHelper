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
- **시크릿 값은 Terraform 에 넣지 않는다.** Terraform 은 Secrets Manager
  **껍데기만** 만들고, 값은 `aws secretsmanager put-secret-value` 로 한 번
  넣는다 (아래 5-C). 값을 코드에 적으면 state 와 plan 출력에 평문으로 남는다.
  ★ **SSM 파라미터 스토어로는 이걸 지킬 수 없었다** — `ignore_changes = [value]`
  는 plan 시점만 바꾸고 `refresh` 가 state 를 실제 값으로 다시 쓴다
  ([ADR-022](../../docs/adr/022-secret-storage.md)).
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

### ★ 이 두 줄은 **매일, 그리고 새 터미널마다** 다시 해야 한다 (2026-11-24 추가)

둘이 서로 다른 이유로 사라진다. 하나만 고치면 다른 하나로 또 막힌다.

| | 무엇이 사라지나 | 수명 | 되살리는 법 |
|---|---|---|---|
| ① | **SSO 액세스 토큰** (`~/.aws/sso/cache/`) | ★ **8시간** | `aws sso login --profile pokehelper` (브라우저가 열린다) |
| ② | **`AWS_PROFILE` 환경변수** | ★ **그 셸에서만** | `$env:AWS_PROFILE = "pokehelper"` |

②가 매번 필요한 이유: 이 PC 에는 **`default` 프로필도 `~/.aws/credentials` 파일도
없다.** `[profile pokehelper]` 하나뿐이라, 환경변수로 지목하지 않으면 Terraform 이
쓸 프로필을 못 찾는다. 새 터미널을 열면 그 지목이 사라진다.

★ **②를 영구히 박아두려면** (새로 여는 터미널에 적용, 현재 셸에는 적용 안 됨):

```powershell
setx AWS_PROFILE pokehelper
```

### ★ 증상 → 원인 대응표 ★

이 구간의 에러 메시지는 **원인을 가리키지 않는다.** 그래서 표로 적어둔다.

| 메시지 | 실제 원인 |
|---|---|
| `No valid credential sources found` + ★ `dial tcp 169.254.169.254:80` | **②가 안 잡혔다.** `169.254.169.254` 는 **EC2 인스턴스 메타데이터** 주소다 — 자격증명 체인이 프로필을 하나도 못 찾아 맨 마지막 후보까지 떨어진 것이다. 노트북은 EC2 가 아니니 당연히 닿지 않는다. **AWS 문제도 네트워크 문제도 아니다** |
| `SSO session ... has expired` / `Token has expired and refresh failed` | **①이 만료됐다.** 8시간이 지났다 |
| `backend/s3` 링크가 같이 나온다 | **state 백엔드**가 먼저 실패한 것이다. 프로바이더가 아니다 — 둘 다 같은 체인을 쓰므로 고치는 법은 같다 |

토큰이 언제 만료되는지는 캐시 파일의 `expiresAt` 에 있다. ★ **그 파일에는 액세스
토큰이 들어 있으니 통째로 출력하지 않는다.**

```powershell
Get-ChildItem ~/.aws/sso/cache/*.json | ForEach-Object {
  $j = Get-Content $_ -Raw | ConvertFrom-Json
  if ($j.expiresAt) { "$($_.Name.Substring(0,8)) : $($j.expiresAt)" }
}
```

> `profile` 을 `backend.hcl` 과 프로바이더에 박아 환경변수 의존을 없앨 수도 있다.
> 지금 그렇게 하지 않은 이유는 `backend.hcl` 을 바꾸면 **`terraform init
> -reconfigure` 가 필요**해서다. 환경변수 한 줄이 백엔드와 프로바이더 **양쪽을
> 한 번에** 덮으므로 더 싸다. 되돌리는 조건: 프로필을 여러 개 쓰게 되면 그때
> 코드로 옮긴다 (`backend.hcl.example` 에 주석으로 남겨뒀다).

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

> ★ `terraform.tfvars` 는 **이제 필수다** (2026-11-23). `dns.tf`·`alb.tf` 가
> `domain_name`·`hosted_zone_id` 를, `observability.tf` 가 `alarm_email` 을
> 참조한다. `terraform.tfvars.example` 을 복사해 셋을 채운다. 도메인 두 개는
> 콘솔 등록이 먼저다 — 3장.

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
| 4 | `iam.tf`, `secrets.tf` | 역할 3개 · ★ **시크릿은 껍데기까지만** (값은 5단계) |
| 5 | `dns.tf`, `alb.tf`, `ecs.tf` | 시크릿 값 주입(5-C) → DB 계정 생성 → `/health` 가 200 |
| 6 | `scheduler.tf`, `observability.tf` | 다음 날 수집이 돌았는가 |

★ **`image_tag` 가 없으면 `plan` 이 멈춰 서서 값을 묻는다** (2026-11-24 추가).
`ecs.tf` 가 생기면서 **기본값 없는 필수 변수**가 하나 늘었다. 기본값을 두지 않은
이유는 `variables.tf` 의 description 에 있다 — `latest` 같은 값을 받아주면
**무엇이 떠 있는지 git 에서 되짚을 수 없다.**

```bash
git rev-parse --short=12 HEAD
```

이 값을 `terraform.tfvars` 의 `image_tag` 에 넣는다. 그리고 그 SHA 의 이미지가
**ECR 에 실제로 올라가 있어야 한다** (5-B). 없으면 태스크가
`CannotPullContainerError` 로 떨어진다.

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

### ★ 5단계 안에서 순서가 고정돼 있다 (2026-11-23 수정)

이 표의 4단계에는 원래 "시크릿 값을 손으로 넣었는가"가 적혀 있었다. **그 자리가
틀렸다.** 6개 키 중 세 개(`DATABASE_URL`·`MIGRATION_DATABASE_URL`·
`DATA_SOURCE_NAME`)가 **RDS 엔드포인트 호스트명**을 품는데, 그 호스트명에는 AWS 가
정하는 임의 문자열이 들어가서 **RDS 를 만들기 전에는 알 수 없다.**

5단계 안쪽 순서는 아래로 고정된다. 각 화살표가 "앞의 것이 없으면 뒤의 것이
불가능하다"는 뜻이다.

```
data.tf apply            # RDS 엔드포인트가 생긴다
  → 5-C 시크릿 값 6키     # DSN 세 개에 그 엔드포인트를 넣는다
  → ecs.tf apply          # 실행 역할이 시크릿을 읽어 태스크를 띄운다
  → DB 계정 3개 생성       # ★ 태스크 안에서만 할 수 있다 (아래)
  → /health 200
```

★ **DB 계정 생성이 태스크 안에서만 가능한 이유.** RDS 는 프라이빗 서브넷 +
`publicly_accessible = false` 이고, 보안그룹이 **태스크 SG 에서만** 인바운드를
받는다. NAT 도 배스천도 없다. 그래서 `backend/sql/roles.sql` 을 돌릴 수 있는
곳은 ECS 태스크 **안**뿐이고, 그래서 운영 이미지가 `psql` 과 `sql/` 을 들고
있어야 한다 (`backend/Dockerfile` 의 `dbclient` 스테이지 주석).

★ **정정 (2026-11-24).** 이 자리에는 "그 사이 태스크는 뜨지만 헬스체크가
실패한다 — `champs_app` 이 아직 없으니 당연하다"라고 적혀 있었다. **틀렸다.**

`/health` 는 **DB 를 건드리지 않는다.** `app/api/v1/health.py` 가 상태 문자열
셋(`status`·`service`·`version`)만 돌려주는 순수 liveness 체크다(독스트링에
"Phase 0 시점에서는 프로세스 생존 여부만 본다"고 적혀 있다). 따라서 **빈 DB 로도
타깃그룹이 정상 판정을 내리고 `/health` 는 200 이 나온다.**

이 정정이 중요한 이유는 둘이다.

1. **DB 계정을 만들 창이 열린다.** 태스크가 헬스체크 실패로 계속 교체되면 ECS
   Exec 로 들어가 `roles.sql` 을 돌릴 틈이 없다. 실제로는 태스크가 안정돼 있다.
2. **`/health` 200 은 "DB 가 붙었다"를 뜻하지 않는다.** 수락 기준으로 쓸 때 그
   차이를 알고 써야 한다. DB 연결까지 보는 readiness 체크는 아직 없다.

구체적인 ECS Exec 명령은 **실제로 돌려본 뒤에** 5-D 에 쓴다. 검증하지 않은
절차를 먼저 적는 것이 바로 JOURNAL 2026-11-23("README 가 지킬 수 없는 약속을
먼저 적어두고 있었다")에 기록된 실수다.

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
| **실행** 역할 | ECS 에이전트 (컨테이너 **밖**) | 태스크가 **뜨기 전** | ECR pull · 로그 스트림 생성 · 시크릿을 환경변수로 주입 |
| **태스크** 역할 | 컨테이너 **안**의 앱 코드 | 도는 동안 | 앱이 호출하는 AWS API (우리는 ECS Exec 만) |

★ "시크릿을 못 읽는다"는 거의 항상 **실행 역할** 문제다. 이름만 보고
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

## 5-C. 시크릿 값 넣기 (★ `data.tf` apply 직후, 최초 1회)

`secrets.tf` 가 만드는 것은 **빈 시크릿 하나**다. 값은 Terraform 이 모른다
([ADR-022](../../docs/adr/022-secret-storage.md)). 아래는 **이 디렉터리에서**
실행한다.

★ **RDS 를 만든 뒤에 한다.** DSN 세 개가 엔드포인트 호스트명을 품고, 그 이름은
RDS 가 생기기 전에는 알 수 없다 (4장의 순서 설명 참조). 엔드포인트는
`terraform output db_endpoint` 로 얻는다.

```powershell
# 1. 키만 있고 값이 빈 템플릿을 저장소 **밖**(임시 폴더)에 받는다
$f = "$env:TEMP\pokehelper-secret.json"
$json = terraform output -json app_secret_template | Out-String
[IO.File]::WriteAllText($f, $json, (New-Object System.Text.UTF8Encoding $false))

# 2. 각 키가 무엇인지 읽는다
terraform output app_secret_keys_documented

# 3. 값을 채운다
notepad $f

# 4. 넣는다
aws secretsmanager put-secret-value `
  --secret-id (terraform output -raw app_secret_name) `
  --secret-string "file://$f" `
  --profile pokehelper

# 5. ★ 지운다. 평문 JSON 을 디스크에 남기지 않는다
Remove-Item $f
```

### ★ `-target` 으로 apply 했으면 1번이 실패한다 (2026-11-24 추가)

```
Error: Output "app_secret_template" not found
```

4장처럼 `-target` 으로 단계별 apply 를 했으면 **이 출력은 state 에 없다.**
`-target` 은 타깃 자원에 **의존하지 않는 출력을 평가하지 않는데**,
`app_secret_template` 과 `app_secret_keys_documented` 는 `local.app_secret_keys`
만 참조하고 자원을 참조하지 않는다. (`app_secret_name`·`app_secret_arn` 은
`aws_secretsmanager_secret.app` 을 참조하므로 **있다** — 그래서 4번은 그냥 된다.)

★★ **그리고 1번이 실패해도 그 뒤가 전부 성공한다** ★★

`$json` 이 빈 값이 되고 → 빈 파일이 만들어지고 → notepad 에 빈 파일이 열리고 →
`put-secret-value` 가 **그 빈 내용을 그대로 올린다.** Secrets Manager 는
**내용이 JSON 인지 검사하지 않으므로** `VersionId` 가 정상적으로 돌아온다.
실패가 조용히 성공으로 포장된다. 실제로 그렇게 됐다
(JOURNAL 2026-11-24).

대체 절차 — 키 이름을 `secrets.tf` 에서 읽는다. 손으로 타이핑하지 않는 원칙은
그대로다.

```powershell
$f = "$env:TEMP\pokehelper-secret.json"
$lines = Get-Content .\secrets.tf
$start = ($lines | Select-String -Pattern 'app_secret_keys\s*=\s*\{' | Select-Object -First 1).LineNumber
$keys = @()
for ($i = $start; $i -lt $lines.Count; $i++) {
  if ($lines[$i] -match '^\s*\}') { break }
  if ($lines[$i] -match '^\s*([A-Z][A-Z0-9_]*)\s*=') { $keys += $Matches[1] }
}
"읽은 키 $($keys.Count) 개: $($keys -join ', ')"   # ★ 6 이 아니면 멈춘다

$obj = [ordered]@{}
foreach ($k in $keys) { $obj[$k] = "" }
[IO.File]::WriteAllText($f, ($obj | ConvertTo-Json), (New-Object System.Text.UTF8Encoding $false))
notepad $f
```

### ★ 올린 뒤 **반드시** 검증한다 (2026-11-24 추가)

`put-secret-value` 의 성공은 "올라갔다"만 뜻한다. **내용이 맞다는 뜻이 아니다.**
아래는 **값을 출력하지 않고** 키 이름과 길이만 본다.

```powershell
$s = aws secretsmanager get-secret-value --secret-id (terraform output -raw app_secret_name) `
  --query SecretString --output text --profile pokehelper
"줄 수: $($s.Length)"   # ★ 바이트가 아니다 — PowerShell 이 출력을 줄 배열로 받는다
try { ($s | ConvertFrom-Json).PSObject.Properties | ForEach-Object { "  $($_.Name) = $($_.Value.Length)" } }
catch { "★ JSON 파싱 실패 - 내용이 JSON 이 아니다" }
```

★ **길이만으로 DSN 구조가 맞는지 산술로 확인된다.** 값을 화면에 띄우지 않고
검증할 수 있다는 것이 이 방법의 요점이다. 호스트명이
`pokehelper-prod-db.<12자>.ap-northeast-2.rds.amazonaws.com`(64자), 비밀번호가
32자, DB 이름이 `champs` 일 때:

| 키 | 계산 | 기대 |
|---|---|---|
| `DATABASE_URL` | `postgresql+asyncpg://`21 + `champs_app`10 + `:`1 + 32 + `@`1 + 64 + `:5432`5 + `/champs`7 | **141** |
| `MIGRATION_DATABASE_URL` | 위 + (`champs_migrate`14 − `champs_app`10) | **145** |
| `DATA_SOURCE_NAME` | `postgresql://`13 + `champs_read`11 + `:`1 + 32 + `@`1 + 64 + 5 + 7 + `?sslmode=require`16 | **150** |

한 글자라도 다르면 `+asyncpg` 누락, 포트 중복, `sslmode` 오타 중 하나다.
**키가 6개가 아니거나 길이가 0 이면 다시 올린다.**

★ **값을 명령에 직접 적지 않는다.** 템플릿을 `terraform output` 에서 받으므로
키 이름을 손으로 칠 일이 없고, 오타로 키가 어긋나 **앱이 기본값으로 조용히
뜨는 일**이 생기지 않는다.

★ **`Out-File -Encoding utf8` 을 쓰지 마라.** PS 5.1 은 BOM 을 붙이고, BOM 이
붙은 JSON 은 `file://` 로 읽힐 때 파싱이 깨진다 (ECR `config.json` 에서 같은
함정을 밟았다 — JOURNAL 2026-11-23). `utf8NoBOM` 은 **PS 7 이상**에만 있어서
5.1 에서는 쓸 수 없다. 그래서 `[IO.File]::WriteAllText` 를 쓴다.

★ **`--secret-string (Get-Content $f -Raw)` 로 바꾸지 마라.** PS 5.1 은 네이티브
프로그램에 인자를 넘길 때 문자열 안의 따옴표를 이스케이프하지 않아
`{"A":"b"}` 가 `{A:b}` 로 도착한다. `file://` 로 넘기면 그 경로를 피한다.

**값을 바꿀 때도 같은 절차다.** 한 키만 바꾸려면 현재 값을 받아 고쳐야 한다:

```powershell
$cur = aws secretsmanager get-secret-value --secret-id (terraform output -raw app_secret_name) `
  --query SecretString --output text --profile pokehelper
[IO.File]::WriteAllText($f, $cur, (New-Object System.Text.UTF8Encoding $false))
```

이게 "시크릿 하나에 JSON 으로 담는다"($0.40)의 대가다. 6개로 쪼개면
키별로 독립 갱신되지만 월 $2.40 이 된다.

★ **빠뜨리면 조용히 넘어가지 않는다.** 값이 없으면 ECS 가 컨테이너를 띄우기
전에 시크릿을 못 가져와 `ResourceInitializationError` 로 실패한다. 플레이스홀더를
심지 않았으므로 "가짜 값으로 떠 있는" 상태가 없다.

---

## 5. 검사

```bash
terraform fmt -check -recursive
terraform validate

# ★ 보안그룹 규칙 — 퍼블릭 서브넷을 골랐으므로 이게 유일한 방어선이다
docker compose exec backend uv run pytest tests/integration/test_terraform_security_groups.py

# ★ IAM — 넓어지는 방향을 막는다. 특히 iam:PassRole
docker compose exec backend uv run pytest tests/integration/test_terraform_iam.py

# ★ 시크릿 — 값이 코드·plan·state 에 들어오는 경로를 막는다
docker compose exec backend uv run pytest tests/integration/test_terraform_secrets.py

# ★ DNS·인증서 — 자동 갱신이 끊기거나 존이 둘이 되는 방향을 막는다
docker compose exec backend uv run pytest tests/integration/test_terraform_dns.py

# ★ ALB — 평문 서비스와 라우팅 어긋남을 막는다. 80 포트가 forward 가 되는 것이 핵심
docker compose exec backend uv run pytest tests/integration/test_terraform_alb.py

# ★ ECS — 시크릿이 평문 환경변수로 새는 것, 클러스터 이름이 iam.tf 와 어긋나는 것,
#   컨테이너 포트가 타깃그룹과 달라져 영구 503 이 되는 것을 막는다
docker compose exec backend uv run pytest tests/integration/test_terraform_ecs.py

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

여기에 아래가 더해진다.

| 항목 | 금액 | 언제 시작되나 |
|---|---|---|
| Secrets Manager 시크릿 2개 | **월 $0.80** | `secrets.tf` apply (RDS 쪽은 `data.tf`) |
| ★ CloudWatch 로그 그룹 3개 | 수집 $0.76/GB · 저장 $0.0314/GB·월 | `ecs.tf` apply. **보존 14일**이라 누적이 멈춘다 |
| ★ 퍼블릭 호스팅 존 1개 | **월 $0.50** | **도메인 등록 시점.** 일수 비례 계산이 없다 |
| 도메인 등록 | 연 $3~16 (TLD 에 따라) | 등록 시 즉시 결제, **환불 없음** |
| ACM 인증서 | **$0** | ALB 에 붙이는 공인 인증서는 무료다 |
| DNS 쿼리 | 100만건당 $0.40 ≈ $0 | alias 조회는 **과금되지 않는다** |

★ 호스팅 존 $0.50 은 ADR-020 의 월 $98 에 **없는 항목**이다. ALB·ECS 보다
먼저 시작되는 유일한 과금이다.

시크릿 두 개는 같은 이유로 낸 비용이다 — 직접 만들면 **값이 state 에 평문으로
들어간다.**

| 시크릿 | 무엇 | 누가 만드나 |
|---|---|---|
| `…-prod-db` (RDS 관리) | DB 마스터 비밀번호 | RDS 가 생성 (`manage_master_user_password`) |
| `pokehelper-prod/app` | 런타임 시크릿 6키 | Terraform 이 껍데기, 값은 손으로 (ADR-022) |

★ 후자를 6개로 쪼개면 $2.40 이 된다. 한 시크릿에 JSON 으로 담아 $0.40 으로
줄인 대가는 **한 키만 바꿀 때도 JSON 전체를 다시 넣어야 한다**는 것이다.

청구 알람이 `observability.tf` 에 들어 있다. 임계치는
`monthly_budget_usd`(기본 150)다. **비용 절감 장치가 아니라 사고 방지
장치**다 — 로그 폭증이나 데이터 전송 증가는 조용히 일어난다.
