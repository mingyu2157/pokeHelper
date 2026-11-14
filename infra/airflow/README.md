# Airflow — 수집 스케줄링

평소에는 뜨지 않는다. 필요할 때만 profile 로 올린다.

```bash
docker compose --profile airflow up -d      # 올리기 (컨테이너 5개 추가)
docker compose --profile airflow down       # 내리기
```

UI: <http://localhost:8080> — 계정 `airflow` / `airflow` (로컬 전용)

메모리는 약 **930MB** 쓴다 (scheduler 385 / apiserver 263 / dag-processor 224 / db 55).

---

## 무엇이 어디서 도는가

```
[airflow-scheduler]  "06:00 이다, 수집할 시간"
        │
        │  /var/run/docker.sock 으로 명령
        ▼
[새 컨테이너: pokehelper-backend]  uv run python -m scripts.run_ingest
        │                            ↕  pokehelper_default 네트워크
        │                           [db]
        ▼
   종료 코드  →  Airflow 가 성공/실패 판정
```

**Airflow 는 우리 코드를 import 하지 않는다.** DAG 는 `DockerOperator` 로
`pokehelper-backend` 이미지를 별도 컨테이너로 띄운다. 이유는 [ADR-012](../../docs/adr/012-airflow-docker-operator.md)
에 있다 — 요약하면 Airflow 3.4 가 SQLAlchemy 2.0.55 를 고정하는데 우리는 2.1.8 을 쓴다.

그래서 **Airflow 없이도 수집이 돈다.**

```bash
docker compose exec backend uv run python -m scripts.run_ingest --limit 50
```

---

## 컨테이너 다섯 개

| 서비스 | 역할 | 없으면 |
|---|---|---|
| `airflow-db` | Airflow 자체 메타데이터 (우리 `champs` DB 와 **분리**) | 실행 이력을 저장할 곳이 없다 |
| `airflow-init` | DB 마이그레이션 + 관리자 계정. 한 번 돌고 종료 | 스키마가 없어 나머지가 뜨지 않는다 |
| `airflow-apiserver` | 웹 UI (`:8080`). 2.x 의 `webserver` | 실패를 눈으로 볼 수 없다 |
| `airflow-scheduler` | 시각을 보고 작업 실행 | 아무것도 안 돈다 |
| `airflow-dag-processor` | DAG 파일 파싱. **Airflow 3 에서 분리됨** | DAG 가 목록에 안 나타난다 |

공식 예제의 `worker`·`triggerer`·`flower` 는 넣지 않았다. 각각 CeleryExecutor용,
지연 실행 연산자용, Celery 모니터링용인데 우리는 `LocalExecutor` 를 쓰고 작업이 2개다.

**Airflow DB 를 분리한 이유**: Airflow 는 자기 테이블을 30개 넘게 만든다. `champs` 에
섞으면 Alembic `--autogenerate` 가 그걸 "모델에 없는 테이블"로 보고 `DROP TABLE` 을
제안한다. 모르고 적용하면 Airflow 실행 이력이 날아간다.

---

## DAG: `champions_daily`

```
ingest_singles  --성공하면-->  cleanup_expired
```

매일 06:00. **새로 올라온 DAG 는 멈춤 상태**다 (`DAGS_ARE_PAUSED_AT_CREATION`).
UI 에서 토글을 켜야 스케줄이 돈다 — 실수로 바로 수집이 도는 걸 막는다.

### 알아둘 설정

| 설정 | 값 | 끄거나 바꾸면 |
|---|---|---|
| `catchup` | `False` | `start_date` 부터 오늘까지 **소급 실행**. 과거 사용률은 받을 수도 없는데 같은 현재 데이터를 날짜만 바꿔 중복 수집하고 출처에 요청만 퍼붓는다 |
| `max_active_runs` | `1` | 수집이 느려지면 두 실행이 겹쳐 같은 스냅샷을 두고 다툰다 |
| `auto_remove` | `"success"` | 실패한 컨테이너까지 지워져 `docker logs` 로 볼 수 없다 |
| `mount_tmp_dir` | `False` | Windows/Docker Desktop 에서 경로 변환 때문에 작업이 실패한다 |
| `network_mode` | `pokehelper_default` | 작업 컨테이너가 `db` 호스트명을 못 찾는다 |
| `execution_timeout` | 15분 / 5분 | 출처가 응답을 안 주면 작업이 영원히 매달린다 |

### 종료 코드와 재시도

| 코드 | 뜻 | 재시도가 의미 있나 |
|---|---|---|
| `0` | 활성 스냅샷 갱신됨 | — |
| `1` | 수집 실패 (네트워크·HTTP) | **있다.** 10분 뒤 되면 성공할 수 있다 |
| `2` | 적재는 됐지만 **활성화 못 함** | 없다. 사람이 매핑을 고쳐야 한다 |

지금은 둘을 구분하지 않고 `retries=2` 로 둔다. `2` 를 재시도해도 멱등해서 해롭지 않고,
구분하려면 종료 코드로 분기하는 로직이 DAG 에 들어와 "DAG 는 얇게"라는 전제가
흔들린다 ([ADR-011](../../docs/adr/011-scheduler-staging.md)).

`2` 로 실패해도 **데이터 유실은 없다.** 이전 스냅샷이 계속 서비스한다.

---

## 자주 쓰는 명령

```bash
# DAG 가 읽혔는지
docker compose exec airflow-scheduler airflow dags list
docker compose exec airflow-scheduler airflow dags list-import-errors

# 작업 하나만 즉시 실행 (스케줄 무시, DB 에 실행 이력 남지 않음)
docker compose exec airflow-scheduler airflow tasks test champions_daily cleanup_expired
docker compose exec airflow-scheduler airflow tasks test champions_daily ingest_singles

# DAG 전체를 지금 실행
docker compose exec airflow-scheduler airflow dags trigger champions_daily

# 로그
docker compose logs airflow-scheduler --tail 50
```

---

## 막히는 곳

**DAG 가 목록에 없다** — `airflow-dag-processor` 가 떠 있는지 본다. 파싱 오류는
`airflow dags list-import-errors` 로 나온다. 파일을 고쳐도 반영까지 30초쯤 걸린다.

**작업이 `db` 를 못 찾는다** — `POKEHELPER_NETWORK` 가 실제 네트워크 이름과 다르다.
`docker network ls | grep poke` 로 확인한다. compose 프로젝트명(디렉터리명)이 바뀌면
이 값도 바뀐다.

**이미지를 못 찾는다** — `docker compose build backend` 로 이미지를 만든다.
DockerOperator 는 **이미지에서** 컨테이너를 띄우므로, compose 의 바인드 마운트와 달리
코드가 이미지 안에 들어 있어야 한다. 코드를 고쳤으면 다시 빌드한다.

**`/var/run/docker.sock` 권한 오류** — Docker Desktop 이 떠 있는지 본다.
Git Bash 에서 `docker run -v /var/run/...` 를 직접 칠 때는 경로가 Windows 경로로
변환돼 실패한다 (`MSYS_NO_PATHCONV=1` 또는 `//var/run/...`). compose 파일에서는
이 문제가 없다.
