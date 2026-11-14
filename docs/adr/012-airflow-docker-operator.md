# ADR-012: Airflow가 우리 코드를 import하지 않고 DockerOperator로 격리 실행

- 상태: 채택 (2026-11-14)
- 관련: Phase 2-6, `infra/airflow/`, `docker-compose.yml`
- 관련 결정: [ADR-006](006-data-pipeline-stack.md) (파이프라인 스택), [ADR-011](011-scheduler-staging.md) (CLI 먼저)

## 맥락

[ADR-011](011-scheduler-staging.md)에서 CLI를 먼저 만들고 Airflow가 그것을 호출하기로
했다. 이번 단계에서 Airflow를 실제로 붙이면서 **"어떻게 호출할 것인가"** 를 정해야 했다.

Airflow를 처음 붙일 때 가장 자연스러운 방법은 Airflow 컨테이너에 우리 코드와
의존성을 설치하고 DAG에서 `from app.services.ingest import ingest` 하는 것이다.
그런데 버전을 맞춰보니 문제가 있었다.

```
Airflow 3.4.0 constraints:  SQLAlchemy==2.0.55
우리 백엔드:                 SQLAlchemy 2.1.8
```

Airflow는 의존성 수십 개를 constraints 파일로 고정한다. 한 환경에 둘을 넣으면
**둘 중 하나는 자기가 요구하지 않은 버전으로 돈다.** 지금은 충돌이 하나지만,
양쪽 의존성이 늘수록 늘어난다. 그리고 그 충돌은 **런타임에 드러난다** — 설치는
되는데 어느 쿼리에서 예외가 나는 식이다.

또 하나 걸린 것은 `CLAUDE.md` 1-4의 원칙이다 — **"수집이 깨져도 계산 기능은 100%
살아있어야 한다."** 수집 로직이 Airflow 환경에 묶이면, Airflow를 띄우지 않고는
수집을 한 번 돌려볼 수도 없게 된다.

## 선택지

### A안: Airflow 이미지에 우리 코드·의존성을 설치하고 PythonOperator로 직접 호출
- 장점:
  - 컨테이너를 추가로 띄우지 않아 작업 시작이 빠르다 (수백 ms).
  - XCom으로 파이썬 객체를 그대로 주고받을 수 있다.
  - Airflow 문서의 가장 흔한 예제 형태라 자료가 많다.
- 단점:
  - **의존성 충돌이 확정적이다** (SQLAlchemy 2.0.55 vs 2.1.8). 둘 중 하나가 진다.
  - Airflow를 올려야만 수집을 돌려볼 수 있다. `CLAUDE.md` 1-4와 어긋난다.
  - 우리 코드를 고칠 때마다 Airflow 이미지를 다시 빌드해야 한다.

### B안: BashOperator로 `docker exec backend ...` 호출
- 장점: 이미 떠 있는 backend 컨테이너를 쓰므로 시작이 빠르다.
- 단점:
  - **backend 컨테이너가 떠 있어야만 돈다.** 웹 서버가 죽으면 수집도 못 한다.
    전혀 다른 두 가지(API 서빙, 배치 수집)가 한 컨테이너 수명에 묶인다.
  - 수집이 웹 서버와 CPU·메모리를 나눠 쓴다.
  - Airflow 이미지에 docker CLI를 따로 설치해야 한다.

### C안: DockerOperator로 `pokehelper-backend` 이미지를 별도 컨테이너로 실행
- 장점:
  - **의존성이 섞이지 않는다.** 두 환경이 서로를 모른다.
  - 작업마다 깨끗한 컨테이너에서 시작한다. 이전 실행의 상태가 남지 않는다.
  - Airflow 없이도 같은 명령이 돈다 (`docker compose exec backend ...`).
  - backend 웹 서버가 죽어도 수집은 돈다.
- 단점:
  - **`/var/run/docker.sock`을 마운트해야 한다.** Airflow 컨테이너가 호스트의
    Docker 전체를 조종할 수 있다는 뜻이다. 사실상 호스트 권한에 준한다.
  - 작업마다 컨테이너를 띄워 2초쯤 더 걸린다 (우리 작업은 분 단위라 무의미).
  - 코드를 고치면 **이미지를 다시 빌드**해야 반영된다. compose의 바인드 마운트에
    익숙하면 놓치기 쉽다.
  - 네트워크 이름(`pokehelper_default`)을 DAG가 알아야 한다. 프로젝트명이 바뀌면 깨진다.

## 결정

**C안.**

세부 사항:

1. `infra/airflow/Dockerfile` — 공식 이미지에 `apache-airflow-providers-docker`
   하나만 더한다. 버전은 constraints가 고정한 값(4.6.1)을 쓴다.
2. 컨테이너 다섯 개: `airflow-db` / `airflow-init` / `airflow-apiserver` /
   `airflow-scheduler` / `airflow-dag-processor`.
   공식 예제의 `worker`·`triggerer`·`flower`는 넣지 않는다 — `LocalExecutor`를 쓰고
   지연 실행 연산자가 없다.
3. **Airflow 메타데이터 DB를 `champs`와 분리한다.** Airflow는 자기 테이블을 30개 넘게
   만든다. 같은 DB에 섞으면 Alembic `--autogenerate`가 그걸 "모델에 없는 테이블"로 보고
   `DROP TABLE`을 제안하고, 모르고 적용하면 실행 이력이 날아간다.
4. **compose profile로 감싼다.** `docker compose up`은 지금처럼 4개만 띄우고,
   `--profile airflow`를 줘야 Airflow가 올라온다 (추가 메모리 약 930MB).
5. `auto_remove="success"` — 실패한 컨테이너는 남긴다. `docker logs`로 봐야 한다.
6. 종료 코드 `1`(네트워크)과 `2`(활성화 실패)를 **구분하지 않고** 똑같이 재시도한다.
   `2`는 재시도해도 같지만 멱등해서 해롭지 않고, 구분하려면 분기 로직이 DAG에 들어와
   ADR-011의 "DAG는 얇게"가 흔들린다.

## 근거

<!--
  ★ 사용자가 직접 쓴다. Claude가 지어내지 않는다 (CLAUDE.md 10-5).
  참고 질문 (답하지 않아도 된다):
  - docker.sock 마운트의 위험을 어디까지 받아들였는가?
  - 의존성 충돌을 피하는 것과 Airflow 문서의 일반적 형태를 따르는 것 중
    왜 전자를 택했는가?
-->

## 포기한 것

- **보안 경계.** `/var/run/docker.sock`을 마운트한 컨테이너는 호스트의 모든 컨테이너를
  만들고 지우고 들여다볼 수 있다. 로컬 개발이라 받아들였지만, 이 구성을 그대로
  운영에 올리면 안 된다.
- **Airflow 문서의 주류 형태.** 대부분의 예제는 PythonOperator로 코드를 직접 부른다.
  DockerOperator 자료는 상대적으로 적어서, 막혔을 때 찾아볼 글이 적다.
- **XCom으로 객체 주고받기.** 작업 사이에 파이썬 객체를 넘길 수 없다. 지금은 작업이
  독립적이라 문제없지만, 나중에 "수집 결과를 다음 작업에 넘기기"가 필요하면
  파일이나 DB를 거쳐야 한다.
- **즉시 반영.** 코드를 고치면 이미지를 다시 빌드해야 Airflow가 새 코드를 쓴다.
  개발 중 혼동하기 쉬운 지점이다.

## 되돌리는 조건

- **AWS로 올라갈 때 반드시 재검토한다** (Phase 6). ECS에서 docker.sock 마운트는
  불가능하거나 매우 위험하다. `EcsRunTaskOperator` 또는 관리형 Airflow(MWAA)로 바꾼다.
  **이 ADR은 로컬 개발 구성에 한정된다.**
- **작업 시작 지연이 문제가 되면** (초 단위 작업이 많아지면) A안을 다시 본다.
  그때는 의존성 충돌을 감수할지, 수집 전용 경량 패키지를 분리할지 정해야 한다.
- **Airflow와 우리 백엔드의 의존성이 우연히 같아지면** A안의 단점이 사라진다.
  다만 그건 운에 기대는 것이라, 같아졌다는 이유만으로 옮기지는 않는다.
- **compose 프로젝트명이 바뀌면** `POKEHELPER_NETWORK` 기본값이 깨진다.
  환경변수로 덮을 수 있게 해뒀지만, 자주 바뀐다면 네트워크를 명시적으로 선언한다.

## 결과

<!-- 나중에 채운다. 실제로 어땠는지. 틀렸으면 틀렸다고 쓴다. -->
