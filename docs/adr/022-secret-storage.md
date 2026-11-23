# ADR-022: 운영 시크릿을 어디에 두는가 — Secrets Manager 시크릿 1개 + JSON 키

- 상태: 채택 (2026-11-23)
- 관련: Phase 6, `infra/terraform/secrets.tf`, `infra/terraform/iam.tf`,
  `infra/terraform/README.md` 0장·5-C
- 관련 결정: [ADR-015](015-least-privilege-db-accounts.md) (DB 계정 분리 — 시크릿이
  담는 값이 그 계정들의 DSN 이다),
  [ADR-016](016-observability-stack.md) (Grafana 관리자 자격증명),
  [ADR-019](019-deployment-target-and-prerequisites.md) (`METRICS_TOKEN` 이 비면 기동 거부),
  [ADR-020](020-production-topology.md) (운영 구성·비용),
  [ADR-021](021-aws-access-and-least-privilege.md) (실행 역할이 이 시크릿을 읽는다)

## 맥락

운영 태스크에 주입해야 하는 값이 6개다. 전부 **평문으로 남으면 안 되는 것**이다.

| 키 | 무엇 | 유출되면 |
|---|---|---|
| `DATABASE_URL` | 앱 런타임 DSN (`champs_app`) | DB 에 DML 가능 |
| `MIGRATION_DATABASE_URL` | alembic DSN (`champs_migrate`) | **DDL 가능** |
| `METRICS_TOKEN` | `/metrics` 보호 토큰 | 경로별 지연·오류율 노출 |
| `DATA_SOURCE_NAME` | postgres-exporter DSN (`champs_read`) | DB 읽기 |
| `GF_SECURITY_ADMIN_USER` | Grafana 관리자 | 대시보드 전체 |
| `GF_SECURITY_ADMIN_PASSWORD` | Grafana 관리자 비밀번호 | 같음 |

README 0장은 처음부터 **"시크릿 값은 Terraform 에 넣지 않는다"** 고 적고 있었고,
그 방법으로 **SSM 파라미터 스토어 + `lifecycle { ignore_changes = [value] }`** 를
전제했다. 무료고 흔한 패턴이다.

`secrets.tf` 를 쓰려고 그 전제를 확인하다 **그게 성립하지 않는다**는 것을 알았다.

★ **`ignore_changes` 는 plan 시점의 동작만 바꾼다.** 설정과 state 가 어긋날 때
plan 에 변경을 띄우지 않는 것이 전부다. **`refresh` 는 실제 객체에 맞춰 state 를
다시 쓴다** — 다른 식이 그 속성을 참조할 수 있기 때문이다. 즉 사람이 손으로 넣은
실제 값이 **다음 refresh 에서 state 에 평문으로 들어온다.**

그리고 우리 state 버킷은 **버전 관리가 켜져 있다**(`bootstrap/main.tf`, state 가
깨졌을 때 되돌릴 유일한 수단이라 켠 것이다). 즉 한 번 들어간 시크릿은 **구 버전에
영구히 남고**, 지우려면 버전을 하나씩 지워야 한다. 보호 장치가 여기서는 반대로
작동한다.

AWS 프로바이더에 "SecureString 값을 state 에 저장하지 말라"는 이슈(#3475)가
**아직 열려 있다.** Terraform 코어에 `no_store` 개념을 넣자는 제안도 있었지만
들어가지 않았다.

따라서 **README 의 약속이 SSM 으로는 "코드에는 안 넣는다"까지만 참이었다.**
CLAUDE.md 12-3 이 금지하는 것 — 하지 않은 보호를 문서에 적는 것 — 에 해당한다.
그래서 저장소를 다시 골라야 했다.

## 선택지

### A안: SSM SecureString 6개 + `ignore_changes = [value]`

- 장점:
  - **무료.** 표준 파라미터는 과금되지 않는다.
  - 가장 흔한 패턴이라 예제가 많다.
  - 키별로 독립 갱신된다 (`put-parameter --overwrite` 하나).
- 단점:
  - ★ **실제 값이 refresh 에서 state 에 평문으로 들어온다.** 위 맥락 참조.
  - ★ **버전 관리된 버킷의 구 버전에 영구히 남는다.** 지우기가 더 어렵다.
  - `aws_ssm_parameter` 는 `value` 가 **필수**라 "값 없는 껍데기"를 만들 수 없다.
    플레이스홀더를 넣어야 하고, 그러면 **빠뜨렸을 때 가짜 값으로 뜬다** —
    `METRICS_TOKEN` 은 비어 있으면 기동을 거부하지만(ADR-019) 플레이스홀더는
    비어 있지 않아서 **그 방어가 통과된다.**
  - README 의 약속을 "코드에는" 으로 좁혀 적어야 한다.

### B안: Secrets Manager 시크릿 **1개** + JSON 6키

- 장점:
  - ★ **값이 `.tf`·plan·state 어디에도 없다.** `aws_secretsmanager_secret` 은
    **껍데기만** 만들고, 값을 담는 자원(`aws_secretsmanager_secret_version`)을
    쓰지 않으면 Terraform 이 값을 아예 모른다. 서드파티 API 키에 쓰는 문서화된
    패턴이다.
  - ★ **플레이스홀더가 없다.** 값을 안 넣으면 ECS 가 컨테이너를 띄우기 전에
    `ResourceInitializationError` 로 **소리 내며** 실패한다. "가짜 값으로 떠 있는"
    상태가 존재하지 않는다.
  - ECS 가 `valueFrom = "<arn>:<키>::"` 로 **JSON 키 하나만** 꺼낼 수 있어
    6개를 하나에 담아도 주입은 키 단위다.
  - 월 **$0.40**. 이미 RDS 마스터 비밀번호로 같은 비용을 내고 있다.
- 단점:
  - ★ **한 키만 바꿀 때도 JSON 전체를 다시 넣어야 한다.** 현재 값을 받아 고쳐
    다시 올리는 왕복이 생기고, 그 과정에서 **평문이 잠깐 디스크에 있다.**
  - 버전이 시크릿 단위라 **키별 로테이션 이력이 없다.**
  - 사람이 JSON 을 손으로 조립한다. 키 오타가 생기면 그 값만 주입되지 않는다.

### C안: Secrets Manager 시크릿 **6개**

- 장점: B안의 장점 전부 + **키별 독립 갱신·로테이션.** JSON 왕복이 없다.
- 단점: 월 **$2.40.** ADR-020 의 예상 $98 에 더해지는 고정비다. 1인 포트폴리오
  프로젝트에서 "키별 로테이션 이력"을 쓸 일이 지금은 없다.

### D안: 시크릿을 Terraform 에서 아예 제외한다

- 장점: 무료고, state 에 아무것도 없다. 가장 단순하다.
- 단점:
  - ★ **무엇이 필요한지가 어느 코드에도 남지 않는다.** 6개 중 하나를 빠뜨린
    것을 **첫 태스크 기동에서야** 알게 되고, 그때도 "어떤 키가 전부인지"를
    확인할 곳이 없다.
  - 테스트로 지킬 대상이 사라진다. 이 프로젝트는 "없어도 안 깨지는 것"을
    테스트로 지켜왔는데(ADR-013·ADR-015·ADR-020 세부사항 8), 자원이 없으면
    검사할 소스가 없다.

## 결정

**B안 — Secrets Manager 시크릿 1개(`pokehelper-prod/app`) + JSON 6키.**

세부 사항:

1. **Terraform 은 `aws_secretsmanager_secret` 만 만든다.**
   `aws_secretsmanager_secret_version` 을 쓰지 않는다. 이 **부재**가 설계의 전부고,
   `tests/integration/test_terraform_secrets.py` 가 그 부재를 집행한다.
   추가하면 모든 것이 정상 동작하면서 값만 state 로 들어가기 때문이다.

2. ★ **JSON 키 이름 = 컨테이너에 주입될 환경변수 이름 그대로.**
   `ecs.tf` 의 매핑이 `name = key`, `valueFrom = "${arn}:${key}::"` 로 끝난다.
   이름을 다르게 두면 매핑 표를 손으로 관리해야 하고, 거기서 틀리면 **앱이
   기본값으로 조용히 뜬다.** 테스트가 키가 유효한 환경변수 이름인지 검사한다.

3. **`recovery_window_in_days = 0`.** 기본값 30일(최소 7일)이면 `destroy` 후
   같은 이름으로 `apply` 가 "already scheduled for deletion" 으로 막힌다.
   IaC 왕복이 성립하는 쪽을 골랐다 — `ecr.tf` 의 `force_delete` 와 같은 판단이다.
   대신 **되돌릴 수 없다.** 사람이 넣은 값이고 데이터가 아니라는 전제다.

4. **고객 관리형 KMS 키를 쓰지 않는다** (월 $1). AWS 관리형
   `aws/secretsmanager` 를 쓰면 ECS 문서 표현대로 `kms:Decrypt` 가
   "required only if your secret uses a customer managed key" 라서, **실행 역할에
   그 권한을 주지 않는다.** 두 선택이 묶여 있어 한쪽만 바꾸면 증상이
   "시크릿을 못 가져온다"로 나오고 메시지는 KMS 를 가리킨다. 그 연결을
   `test_terraform_secrets.py::TestNoCustomerManagedKey` 에 적어뒀다.

5. **SSM 파라미터 스토어를 아예 쓰지 않는다.** 비밀이 아닌 설정
   (`APP_ENV`·`LOG_LEVEL`·`CORS_ORIGINS`·`REDIS_URL` 등)은 태스크 정의의
   `environment` 에 직접 넣는다. 그래서 실행 역할에서 `ssm:GetParameter*` 를
   제거했다 — 쓰지 않는 권한은 "좁힌 것"이 아니라 "넓은데 들어갈 곳이 없는 것"이다
   (ADR-021 과 같은 원칙).

6. **값을 넣는 절차를 README 5-C 에 적는다.** 템플릿을 `terraform output` 에서
   받아 채우고, 임시 파일을 지운다. ★ **명령에 가린 값을 적지 않는다** — 키를
   손으로 치지 않으므로 오타가 생기지 않는다.

## 근거

<!-- ★ 사용자가 직접 쓴다. Claude 가 채우지 않는다 (CLAUDE.md 10-5).
     B안을 고른 이유를 본인 말로 적어주세요. -->

## 포기한 것

- ★ **키별 갱신의 편의.** 한 키만 바꿀 때도 JSON 전체를 받아 고쳐 다시 올린다.
  그 왕복 동안 **평문 JSON 이 디스크에 잠깐 존재한다.** README 5-C 가 지우는
  단계를 포함하지만, **지우는 것을 사람이 기억해야 한다** — 테스트로 지킬 수 없다.
- ★ **키별 로테이션 이력.** Secrets Manager 의 버전은 시크릿 단위다. "이 토큰을
  언제 바꿨나"를 키별로 추적할 수 없다. C안의 $2.40 으로 살 수 있었던 것이다.
- **자동 로테이션.** Secrets Manager 의 로테이션 기능은 Lambda 를 요구하고,
  ADR-020 결정 4 에서 VPC Lambda 를 기각했다(NAT 가 되살아난다). DB 비밀번호
  로테이션은 수동이다.
- ★ **"값이 실제로 들어갔는가"의 자동 확인.** 테스트는 `.tf` 소스만 읽는다.
  값이 비었는지는 알 수 없다. 대신 **빠뜨리면 태스크 기동이 실패**하므로 조용히
  넘어가지는 않는다 — ADR-015 처럼 pytest 로 위반을 실행해 확인하는 수준은 아니다.
- **무료 선택지.** A안·D안이 무료였다. 월 $0.40 을 "값이 state 에 안 들어간다"에
  썼다.

## 되돌리는 조건

- ★ **JSON 왕복이 실제로 번거로워지면 C안(시크릿 6개)으로 쪼갠다.** 구체적으로는
  **한 키만 바꾸는 작업을 한 달에 두 번 이상** 하게 되거나, 왕복 중에 **다른 키를
  덮어써 사고가 한 번이라도 나면** 그 시점에 쪼갠다. 월 $2.00 이 그 값보다 싸다.
- **고객 관리형 키가 필요해지면** (계정이 둘 이상이 되어 키 정책으로 교차 계정
  접근을 통제해야 할 때) 세부사항 4를 뒤집고 `iam.tf` 의 `kms:Decrypt` 를
  되살린다. 둘은 같이 바뀐다.
- **Terraform 의 write-only 속성이 쓸 수 있게 되면** A안을 다시 본다.
  Terraform 1.11+ / AWS 프로바이더 5.88+ 에 `secret_string_wo` 같은 쓰기 전용
  속성이 들어왔다. SSM 파라미터에도 같은 것이 생기면 "무료 + state 없음"이
  성립한다. 지금 우리 `required_version` 은 `>= 1.9` 다.
- **비밀이 아닌 환경별 설정이 늘어나 태스크 정의가 비대해지면** SSM 을 설정
  저장소로 **따로** 들인다. 이 ADR 이 기각한 것은 "SSM 에 **시크릿**을 두는 것"
  이고, 설정 저장소로 쓰는 것은 별개 판단이다.

## 결과

<!-- 나중에 채운다. apply 후 실제로 어땠는지, 특히 5-C 절차가 한 번에 됐는지. -->

## 출처

- [HashiCorp Discuss — Why update values in the state when they are ignored?](https://discuss.hashicorp.com/t/why-update-values-in-the-state-when-they-are-ignored/38716)
  (`ignore_changes` 는 plan 시점만 바꾸고 refresh 는 state 를 다시 쓴다)
- [terraform-provider-aws #3475](https://github.com/hashicorp/terraform-provider-aws/issues/3475)
  (SecureString 을 state 에 저장하지 말라 — 미해결)
- [Amazon ECS task execution IAM role](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/task_execution_IAM_role.html)
  (`kms:Decrypt` 는 고객 관리형 키일 때만 필요)
