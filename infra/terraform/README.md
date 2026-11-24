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
| 5 | `dns.tf`, `alb.tf`, `ecs.tf` | 5-C 시크릿 → `/health` 200 → 5-D DB 계정 → 5-E 스키마·시드 |
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

구체적인 ECS Exec 명령은 **5-D** 에 있다. 2026-11-24 에 실제로 돌려 성공한 뒤에
썼다 — 검증하지 않은 절차를 먼저 적는 것이 JOURNAL 2026-11-23("README 가 지킬
수 없는 약속을 먼저 적어두고 있었다")에 기록된 실수다.

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

### ★★ 값을 바꿨으면 **재배포까지 해야 반영된다** (2026-11-24 추가) ★★

**ECS 는 시크릿을 태스크가 뜰 때 한 번만 읽는다.** 그 뒤 시크릿을 바꿔도 **도는
태스크는 옛 값을 계속 들고 있다.** 자동 갱신이 없다.

이걸 빠뜨리면 증상이 **"DB 쪽은 다 맞는데 앱만 인증 실패"** 로 나온다. 실제로
그랬다 — `psql` 로는 새 비밀번호가 멀쩡히 붙는데 API 는 500 이었고, 로그를 보고야
`InvalidPasswordError: password authentication failed for user "champs_app"` 를
확인했다 (JOURNAL 2026-11-24).

```powershell
aws ecs update-service --cluster pokehelper-prod --service pokehelper-prod-backend `
  --force-new-deployment --profile pokehelper
```

이미지는 그대로이고 태스크만 새로 떠서 시크릿을 다시 읽는다.
`deployment_minimum_healthy_percent = 100` 이라 새 태스크가 정상이 된 뒤 옛 것이
내려간다 — 무중단이다. 완료 확인:

```powershell
aws ecs describe-services --cluster pokehelper-prod --services pokehelper-prod-backend `
  --query "services[0].{running:runningCount,deployments:length(deployments)}" --profile pokehelper
```

`deployments: 1` 이면 교체가 끝났다 (`2` 면 진행 중).

★ **일회성 태스크(`run-task`)는 해당 없다.** 띄울 때마다 새로 읽는다. 그래서
교체 뒤에 돌린 마이그레이션·시드는 정상이었고 **서비스만 깨져 있었다.**

★ **ECS Exec 로 들어간 세션도 그 태스크의 환경을 본다.** 재배포하면 옛 태스크가
사라지면서 **세션이 조용히 끊긴다.** 프롬프트의 IP 로 어느 태스크인지 확인한다.

---

**값을 바꿀 때도 JSON 을 올리는 절차는 같다.** 한 키만 바꾸려면 현재 값을 받아
고쳐야 한다:

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

## 5-D. 운영 DB 계정 만들기 (★ `ecs.tf` apply 직후, 최초 1회)

**아래는 2026-11-24 에 실제로 돌려 성공한 절차다.** 돌려보기 전에는 쓰지 않았다
(JOURNAL 2026-11-23, "README 가 지킬 수 없는 약속을 먼저 적어두고 있었다").

★ **왜 이 번거로운 경로뿐인가.** RDS 는 프라이빗 서브넷 + `publicly_accessible
= false` 이고 보안그룹이 **태스크 SG 에서 오는 5432 만** 받는다. NAT 도 배스천도
없다. 그래서 `backend/sql/roles.sql` 을 돌릴 수 있는 곳은 **ECS 태스크 안**
뿐이고, 그래서 운영 이미지가 `psql` 과 `sql/` 을 들고 있다
(`backend/Dockerfile` 의 `dbclient` 스테이지 주석, JOURNAL 2026-11-23).

### 선결 조건 — Session Manager 플러그인

`aws ecs execute-command` 가 요구한다. 없으면
`SessionManagerPlugin is not found` 로 실패한다.

```
https://s3.amazonaws.com/session-manager-downloads/plugin/latest/windows/SessionManagerPluginSetup.exe
```

관리자 권한으로 설치하면 `%PROGRAMFILES%\Amazon\SessionManagerPlugin\bin\` 에
들어간다. ★ **이미 열려 있던 셸은 그 경로를 모른다** — `AWS_PROFILE` 과 같은
종류의 함정이다 (1장의 수명 표). 그 셸에서 계속 쓰려면:

```powershell
$env:PATH += ";C:\Program Files\Amazon\SessionManagerPlugin\bin"
session-manager-plugin   # 성공 문구가 나와야 한다
```

### ★ 창을 두 개 쓴다. 그리고 비밀번호를 화면에 띄우지 않는다 ★

비밀번호가 4개 필요하다 — RDS 마스터 1개(AWS 가 관리) + 우리가 정한 3개.

★ **마스터 비밀번호는 컨테이너 안에서 꺼낼 수 없다.** `iam.tf` 가 실행 역할에서
RDS 마스터 시크릿을 **일부러 제외**했기 때문이다("앱 컨테이너가 DB 마스터
비밀번호를 읽을 수 있는 상태"를 막으려고). 그래서 로컬에서 꺼내 컨테이너 안으로
넘긴다.

★★ **`Set-Clipboard` 로 넘기고, 화면에 출력하지 않는다.** 처음에는 로컬에서
`Write-Output` 으로 찍어 보여주는 절차였고, 그 출력이 그대로 외부에 붙여넣어져
**비밀번호 4개를 교체해야 했다** (JOURNAL 2026-11-24). `read -s` 로 가린 것은
컨테이너 안쪽뿐이었고 **로컬 출력은 가리지 않았다** — 절차 설계의 결함이었다.

| 창 | 무엇 |
|---|---|
| **A** | ECS Exec 세션 (컨테이너 안) |
| **B** | 로컬 AWS CLI. 비밀번호를 클립보드로 넘긴다 |

### 창 B — 열기

```powershell
$env:AWS_PROFILE = "pokehelper"
cd C:\project\pokehelper\infra\terraform

# 마스터 비밀번호 교체가 끝났는지 — "active" 여야 한다 ("rotating" 이면 대기)
aws rds describe-db-instances --db-instance-identifier pokehelper-prod-db `
  --query "DBInstances[0].MasterUserSecret.SecretStatus" --profile pokehelper
```

> ★ 이 시크릿은 **기본 7일마다 자동 교체**된다. 그래서 이 절차를 다시 할 때는
> 매번 새로 꺼내야 한다.

### 창 A — 컨테이너에 들어가기

```powershell
$task = aws ecs list-tasks --cluster pokehelper-prod --service-name pokehelper-prod-backend `
  --query "taskArns[0]" --output text --profile pokehelper
aws ecs execute-command --cluster pokehelper-prod --task $task --container backend `
  --interactive --command "/bin/bash" --profile pokehelper
```

프롬프트가 `root@ip-10-0-x-x:/app#` 로 바뀌면 컨테이너 안이다. ★ `appuser` 가
아니라 `root` 인 것은 정상이다 — ECS Exec 는 SSM 에이전트를 통해 들어가므로
Dockerfile 의 `USER` 와 무관하다.

접속 정보를 환경변수로 둔다 (비밀이 아니다):

```sh
export PGHOST=$(echo pokehelper-prod-db.XXXXXXXX.ap-northeast-2.rds.amazonaws.com)
export PGUSER=champs_master
export PGDATABASE=champs
```

> `PGHOST` 값은 `terraform output db_endpoint` 에서 **`:5432` 를 뗀** 호스트명이다.

### 비밀번호 넘기기 — 네 번 반복한다

패턴은 늘 같다: **창 B 에서 클립보드로 복사 → 창 A 에서 `read -s` → 붙여넣기 →
Enter → 길이 확인.**

★ `read -s` 는 입력을 **화면에 보여주지 않는다.** 붙여넣어도 아무 변화가 없는
것이 정상이고, 그래서 "안 들어갔다"고 느껴 Enter 만 누르기 쉽다. 그러면 **빈
값이 들어간다.** 그래서 매번 길이를 확인한다.

창 B (마스터):
```powershell
(aws secretsmanager get-secret-value --secret-id (terraform output -raw db_master_secret_arn) `
  --query SecretString --output text --profile pokehelper | Out-String | ConvertFrom-Json).password | Set-Clipboard
```
창 A — 치고, **붙여넣고**(오른쪽 클릭 또는 Ctrl+V), Enter:
```sh
read -s -p 'master  : ' PGPASSWORD; export PGPASSWORD; echo
echo "${#PGPASSWORD}"      # ★ 28 이어야 한다 (AWS 가 만드는 길이가 고정이다)
```

창 B (나머지 셋 — 한 번만 준비):
```powershell
$sec = aws secretsmanager get-secret-value --secret-id (terraform output -raw app_secret_name) `
  --query SecretString --output text --profile pokehelper | Out-String | ConvertFrom-Json
function Get-Pw($dsn) { [regex]::Match($dsn, '://[^:/]+:([^@]+)@').Groups[1].Value }
```
그다음 **하나씩** 복사하고, 그때마다 창 A 에서 받는다:
```powershell
Get-Pw $sec.MIGRATION_DATABASE_URL | Set-Clipboard     # 창 A: read -s -p 'migrate : ' MIG; echo
Get-Pw $sec.DATABASE_URL           | Set-Clipboard     # 창 A: read -s -p 'app     : ' APP; echo
Get-Pw $sec.DATA_SOURCE_NAME       | Set-Clipboard     # 창 A: read -s -p 'read    : ' RD;  echo
```
창 A — 넷 다 확인:
```sh
echo "master=${#PGPASSWORD} migrate=${#MIG} app=${#APP} read=${#RD}"
# master=28 migrate=32 app=32 read=32
```

### 창 A — 접속 확인 → `roles.sql`

```sh
psql -c "\conninfo"
```

실측(2026-11-24):

```
You are connected to database "champs" as user "champs_master" on host "..." (address "10.0.11.98") at port "5432".
SSL connection (protocol: TLSv1.3, cipher: TLS_AES_256_GCM_SHA384, compression: off)
```

★ `address` 가 **프라이빗 서브넷 IP** 이고 **TLS 1.3** 으로 붙는다. 둘 다 설계
그대로다.

```sh
psql -v migrate_password="$MIG" -v app_password="$APP" -v read_password="$RD" -f /app/sql/roles.sql
```

★ **`NOTICE: champs_master 에게 champs_migrate 멤버십(INHERIT) 부여`** 는 정상
출력이다. PG16 의 CREATEROLE 동작 때문이다 (`roles.sql` 주석 참조).

★★ **실측에서 `pg_read_all_data` 가 그냥 성공했다.** RDS 마스터는 슈퍼유저가
아니지만 `rds_superuser` 멤버라 그 역할에 대한 ADMIN 옵션을 갖고 있었다. 즉
`roles.sql` 4장의 **대체 경로(기본 권한으로 내려앉기)를 타지 않았고**,
`champs_read` 는 **미래 테이블까지 자동 포함되는 더 강한 보장**을 받았다.
`WARNING ... 기본 권한으로 대체한다` 가 나오면 그건 ADMIN 옵션이 없는 환경이고,
그때는 `champs_migrate` 가 만든 테이블만 포함된다 — 둘을 구분해서 읽어야 한다.

### 창 A — 권한 3종 검증 ★ 이게 5-D 의 본체다

★ **`roles.sql` 을 빈 DB 에서 돌린다**는 점이 중요하다. 그 시점에
`GRANT ... ON ALL TABLES` 의 대상은 **0개**이고, 테이블은 그 뒤에 alembic 이
만든다(5-E). 그래서 앱이 테이블을 읽을 수 있는 근거는 5장의
**`ALTER DEFAULT PRIVILEGES FOR ROLE champs_migrate`** 하나뿐이다 — 그 파일이
직접 "가장 틀리기 쉬운 부분"이라고 적어둔 곳이다. **5-E 를 돌린 뒤** 아래를 한다.

```sh
# ① 앱 계정이 미래 테이블을 읽는가 (= 기본 권한이 동작했는가)
PGPASSWORD="$APP" psql -U champs_app  -c "select count(*) from pokemon"

# ② 읽기 전용 계정도 되는가
PGPASSWORD="$RD"  psql -U champs_read -c "select count(*) from moves"

# ③ ★ 부정 검증 — 앱 계정이 DDL 을 **못 해야** 한다
PGPASSWORD="$APP" psql -U champs_app  -c "create table should_fail (id int)"
```

③의 기대 결과는 **성공이 아니라 실패**다:

```
ERROR:  permission denied for schema public
```

테이블이 만들어지면 ADR-015 의 계정 분리가 깨진 것이다.

### 창 A — 정리

```sh
unset PGPASSWORD MIG APP RD
```

창 B 에서 클립보드도 비운다:

```powershell
Set-Clipboard -Value " "
```

---

## 5-E. 스키마와 시드 올리기 (★ 5-D 직후, 최초 1회)

**2026-11-24 에 실제로 돌려 성공한 절차다.**

### ① `migrate` 태스크 정의를 만든다

★ `ecs.tf` 를 `-target=aws_ecs_service.*` 로 적용했으면 **이 태스크 정의와 그
로그 그룹이 아직 없다.** 서비스가 참조하지 않기 때문이다.

```powershell
terraform apply "-target=aws_ecs_task_definition.migrate"
```

**기대: `2 to add`** — 태스크 정의 + `/ecs/pokehelper-prod/migrate` 로그 그룹.

> ★ 서비스 apply 때 plan 은 로그 그룹 3개를 보여주지만 **2개만 만들어진다.**
> `migrate` 로그 그룹은 `migrate` 태스크 정의만 참조하므로 타깃 그래프에
> 들어오지 않는다. 그래서 그 apply 의 `Plan: 8 to add` 와 `7 added` 가
> 어긋난다 — **plan 과 apply 숫자가 다르면 그냥 넘기지 않는다.**

### ② 네트워크 설정을 출력에서 조립한다

손으로 서브넷 ID 를 적으면 거기서 틀린다. 프라이빗 서브넷을 적으면 태스크가
이미지를 못 받고, 메시지는 서브넷을 가리키지 않는다.

```powershell
$net = terraform output -json task_network_configuration | ConvertFrom-Json
$netcfg = "awsvpcConfiguration={subnets=[$($net.subnets -join ',')],securityGroups=[$($net.security_groups -join ',')],assignPublicIp=ENABLED}"
$netcfg
```

### ③ 스키마 — `alembic upgrade head`

태스크 정의의 기본 `command` 가 그것이므로 덮어쓸 것이 없다.

```powershell
$t = aws ecs run-task --cluster pokehelper-prod --task-definition pokehelper-prod-migrate `
  --launch-type FARGATE --network-configuration $netcfg `
  --query "tasks[0].taskArn" --output text --profile pokehelper

aws ecs describe-tasks --cluster pokehelper-prod --tasks $t `
  --query "tasks[0].{status:lastStatus,stopCode:stopCode,reason:stoppedReason,exit:containers[0].exitCode}" `
  --profile pokehelper

aws logs tail /ecs/pokehelper-prod/migrate --since 15m --profile pokehelper
```

★ **`status: STOPPED` + `exit: 0` 이 성공이다.** `stopCode:
EssentialContainerExited` / `reason: Essential container in task exited` 가 같이
나오는데, 일회성 태스크에서는 **그게 정상 종료**다. 서비스라면 장애 신호지만
여기서는 아니다.

실측 로그:
```
INFO [alembic.runtime.migration] Running upgrade  -> e4425a76c12b, 초기 스키마: 마스터 + 메타 스냅샷
INFO [alembic.runtime.migration] Running upgrade e4425a76c12b -> fe1cfe8d79d0, pokemon_learnset 추가
```

### ④ 시드 — 같은 태스크 정의에 `command` 만 덮어쓴다

★ **`--overrides` 에 JSON 을 직접 넘기지 마라.** PowerShell 5.1 은 네이티브
프로그램에 인자를 넘길 때 문자열 안의 따옴표를 이스케이프하지 않는다
(5-C 의 `--secret-string` 과 같은 함정). **파일로 넘긴다.**

```powershell
$ov = "$env:TEMP\pokehelper-seed-override.json"
$body = '{"containerOverrides":[{"name":"migrate","command":["python","-m","scripts.load_seed_to_db"]}]}'
[IO.File]::WriteAllText($ov, $body, (New-Object System.Text.UTF8Encoding $false))

$t2 = aws ecs run-task --cluster pokehelper-prod --task-definition pokehelper-prod-migrate `
  --launch-type FARGATE --network-configuration $netcfg --overrides "file://$ov" `
  --query "tasks[0].taskArn" --output text --profile pokehelper

aws ecs describe-tasks --cluster pokehelper-prod --tasks $t2 `
  --query "tasks[0].{status:lastStatus,exit:containers[0].exitCode}" --profile pokehelper
aws logs tail /ecs/pokehelper-prod/migrate --since 10m --profile pokehelper

Remove-Item $ov
```

실측 로그:
```
포켓몬 263건 적재 / 기술 516건 적재 / 특성 202건 적재 / 성격 25건 적재
도구 22건 적재 / learnset 16037쌍 적재 / 적재 완료.
```

★ **시드 적재는 `champs_app` 으로 돈다** (`scripts/load_seed_to_db.py` →
`app.db.session.get_engine` → `DATABASE_URL`). 그 계정에는 `TRUNCATE` 권한이
없는데(`roles.sql` 4장이 일부러 안 줬다), 스크립트가 upsert 와 DELETE 만 쓰므로
성립한다. **그 전제가 깨지면 여기서 권한 에러가 난다** — 이 단계가 그걸 검증한다.

### ⑤ 끝났는지 — 창 A 에서

```sh
psql -c "\dt"
psql -c "select version_num from alembic_version"
```

★ 테이블 **10개**가 나오고 `Owner` 가 **전부 `champs_migrate`** 여야 한다.
그게 ADR-015 의 핵심이다 — alembic 만 DDL 을 쥔다.

그리고 **5-D 의 권한 3종 검증을 지금 돌린다.** 테이블이 생긴 뒤에야
`ALTER DEFAULT PRIVILEGES` 가 동작했는지 확인할 수 있다.

### ⑥ ALB 를 통한 end-to-end

```powershell
curl.exe -s -o NUL -w "%{http_code}\n" https://paoriprofessor.click/api/v1/meta/usage
```

★ **404 가 정상이고 그게 성공 신호다.** 수집 데이터가 아직 없어
`NotAvailableError` → 404 로 설계돼 있다(`app/api/v1/meta.py`). **404 는 요청이
백엔드에 닿아 DB 를 조회하고 설계된 응답을 돌려줬다는 뜻**이고, DB 가 안 붙으면
500 이 나온다. 둘을 구분해서 읽어야 한다.

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
