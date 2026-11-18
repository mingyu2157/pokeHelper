# AWS 접근 준비 — 손으로 하는 부분

Terraform 이 AWS API 를 호출하려면 **먼저 자격증명이 있어야 한다.** 그 자격증명은
Terraform 으로 만들 수 없다(닭-달걀). 그래서 이 문서의 절차는 **콘솔에서 손으로**
한다.

결정과 그 이유는 [ADR-021](../../docs/adr/021-aws-access-and-least-privilege.md) 에 있다.
여기는 **무엇을 어떻게** 만 적는다.

> 콘솔 클릭은 코드로 남지 않는다. 남기지 않으면 다음에 재현할 수 없어서 이
> 문서가 있다.

---

## 0. 전제

- AWS CLI **v2** 가 필요하다 (`aws --version` → `aws-cli/2.x`).
  v1 에는 `aws configure sso` 가 없다.
- 리전은 **서울 `ap-northeast-2`** 로 통일한다.

---

## 1. 루트 계정 봉인

루트는 **권한을 제한할 수 없다.** 유출되면 계정 전체를 잃는다.

1. 콘솔 우측 상단 계정명 → **보안 자격 증명**
2. **MFA 할당** → 인증 앱(또는 보안 키) 등록
3. 루트 **액세스 키가 있으면 삭제**한다. 있을 이유가 없다.

이후 루트로는 **결제와 계정 설정만** 한다. 일상 작업에 쓰지 않는다.

---

## 2. IAM Identity Center 켜기

콘솔 → **IAM Identity Center** → **활성화**

- ★ **리전을 `ap-northeast-2` 로 고른다. 나중에 못 바꾼다.**
- 단일 계정이어도 AWS Organizations 가 자동으로 켜진다. 정상이다.

활성화되면 **AWS 액세스 포털 URL** 이 생긴다
(`https://d-xxxxxxxxxx.awsapps.com/start`). 로그인에 쓰므로 적어둔다.

---

## 3. 권한 세트 만들기

**다중 계정 권한** → **권한 세트** → **권한 세트 생성**

| 항목 | 값 |
|---|---|
| 유형 | 사전 정의된 권한 세트 → `AdministratorAccess` |
| 이름 | `PokehelperAdmin` |
| 세션 기간 | `PT4H` (4시간) 정도 |

> ★ **지금 Admin 인 것은 의도다.** 처음부터 좁히면 `apply` 가 중간에 죽어
> **절반만 만들어진 인프라**가 남는다. 전체를 한 번 올린 뒤 **CloudTrail 로
> 측정해서** 좁힌다 (6장). ADR-021 결정 2.

---

## 4. 사용자 만들고 계정에 할당

1. **사용자** → **사용자 추가** → 사용자명·이메일 입력
2. 메일로 온 초대를 수락하고 **MFA 를 등록**한다
3. **AWS 계정** → 내 계정 선택 → **사용자 또는 그룹 할당**
   → 방금 만든 사용자 + `PokehelperAdmin` 권한 세트

---

## 5. CLI 프로필 설정

```bash
aws configure sso
```

입력하는 값:

| 질문 | 값 |
|---|---|
| SSO session name | `pokehelper` |
| SSO start URL | 2장에서 적어둔 포털 URL |
| SSO region | `ap-northeast-2` |
| SSO registration scopes | 기본값 엔터 |
| CLI default client Region | `ap-northeast-2` |
| CLI default output format | `json` |
| CLI profile name | `pokehelper` |

브라우저가 열려 승인을 묻는다. 승인하면 끝.

**확인:**

```bash
aws sso login --profile pokehelper
aws sts get-caller-identity --profile pokehelper
```

`Arn` 이 `arn:aws:sts::<계정ID>:assumed-role/AWSReservedSSO_PokehelperAdmin_.../<사용자>`
처럼 나오면 된다. ★ `assumed-role` 이라는 것이 **임시 자격증명**이라는 뜻이다.
장기 액세스 키를 썼다면 `user/...` 로 나온다.

**Terraform 에 넘기기:**

```bash
export AWS_PROFILE=pokehelper      # Windows PowerShell: $env:AWS_PROFILE = "pokehelper"
terraform plan
```

토큰이 만료되면 `The SSO session has expired` 가 뜬다. `aws sso login --profile
pokehelper` 를 다시 하면 된다.

---

## 6. 권한 좁히기 (전체 구축이 끝난 뒤)

ADR-021 결정 2. **추측으로 좁히지 않는다. 측정해서 좁힌다.**

### 6-1. 양쪽을 다 돌린다

```bash
terraform apply
terraform destroy      # ★ 생략하면 안 된다 (아래 설명)
terraform apply        # 다시 올린다
```

★ **CloudTrail 은 실제로 호출된 것만 보여준다.** `apply` 만 보고 좁히면
`Delete*` 액션이 목록에 없어서 **나중에 `destroy` 가 막힌다.**

### 6-2. 호출된 API 를 뽑는다

별도 trail 을 만들지 않는다. **Event history 는 무료고 90일을 보관한다.**

```bash
aws cloudtrail lookup-events \
  --region ap-northeast-2 \
  --start-time "2026-11-19T00:00:00Z" \
  --query 'Events[].[EventSource,EventName]' \
  --output text --profile pokehelper \
  | sed 's/\.amazonaws\.com//' \
  | awk '{print $1":"$2}' \
  | sort | uniq -c | sort -rn
```

나오는 모양:

```
  34 ec2:DescribeSubnets
  12 ec2:CreateTags
   8 ecs:DescribeServices
   ...
```

이 `서비스:액션` 목록이 그대로 정책의 `Action` 이 된다.

### 6-3. 정책을 만들고 전환한다

1. 뽑은 목록으로 고객 관리형 정책을 만든다 (`PokehelperTerraform`)
2. 권한 세트 `PokehelperTerraform` 을 만들어 그 정책을 붙인다
3. 사용자에게 할당하고 **`PokehelperAdmin` 할당을 뗀다**
4. `terraform plan` → `apply` 를 다시 돌려 **막히는 것이 없는지** 확인한다

막히면 에러 메시지에 **빠진 액션 이름이 그대로 나온다.** 그걸 추가한다.

> ★ **이 확인은 자동화되지 않는다.** ADR-015(DB 권한)에서는 위반을 실행해
> 거부되는지 pytest 로 돌릴 수 있었지만, IAM 은 실제 AWS 에 요청을 보내야 해서
> 테스트로 만들 수 없다. **`apply` 를 돌려보는 것이 유일한 검증**이다.
> ADR-021 `## 포기한 것` 에 적혀 있다.

### 6-4. 결과를 기록한다

ADR-021 의 `## 결과` 에 적는다 — 액션이 **몇 개에서 몇 개로** 줄었는지,
좁힌 뒤 무엇이 빠져서 깨졌는지. 숫자가 있어야 "최소 권한을 적용했다"가
증빙이 된다.

---

## 7. Terraform 이 만드는 역할 (참고)

이 문서가 다루는 것은 **운영자 자격증명**뿐이다. 서비스가 쓸 역할은
`iam.tf` 가 만든다.

| 역할 | 누가 쓰나 | 하는 일 |
|---|---|---|
| **실행 역할** | ECS **에이전트** (컨테이너 밖) | ECR pull · 로그 그룹 쓰기 · **SSM 파라미터 읽어 주입** |
| **태스크 역할** | 컨테이너 **안의 앱** | 거의 없음. ECS Exec 을 쓰면 `ssmmessages:*` |
| **스케줄러 역할** | EventBridge Scheduler | `ecs:RunTask` + `iam:PassRole` |

★ **둘을 혼동하는 것이 ECS 의 가장 흔한 함정이다.** "SSM 파라미터를 못
읽는다"는 거의 항상 **실행 역할** 문제인데, 태스크 역할을 고치고 있게 된다.
증상이 `ResourceInitializationError` 로 나와서 앱 문제처럼 보인다.
