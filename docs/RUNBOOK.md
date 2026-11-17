# RUNBOOK — 장애 1차 대응

로컬/개발 환경 기준이다. 운영 배포 대상은 아직 미결이므로([ADR-001](adr/001-tech-stack.md))
운영 절차는 배포 대상이 정해진 뒤에 추가한다.

## 이 문서의 규칙

**실제로 실행해서 동작을 확인한 명령만 적는다.** 확인하지 않은 것은 추측으로 쓰지 않고
`미검증` 으로 표시해 남긴다. 추측으로 적힌 런북은 장애 시점에 한 번 더 시간을 쓰게 만든다.

| 표시 | 뜻 |
|---|---|
| ✅ **검증** | 증상을 재현했고 확인 명령·조치를 실제로 실행했다 |
| ⚠️ **부분 검증** | 확인 명령은 실행했지만 증상 자체는 재현하지 않았다 |
| ❌ **미검증** | 재현 수단이 아직 없다. 적힌 내용은 설계 의도일 뿐이다 |

마지막 검증: 2026-11-17

---

## 0. 먼저 알아야 할 두 가지

### `uv run` 을 빼면 안 된다

dev 이미지는 `/app/.venv/bin` 을 `PATH` 에 넣지 않는다 (prod 타깃만 넣는다).

```bash
docker compose exec backend uv run alembic current   # ✅
docker compose exec backend alembic current          # ✗ executable file not found in $PATH
```

`pg_dump`·`psql`·`pg_isready` 는 OS 패키지라 `uv run` 없이 바로 된다.

### Windows(Git Bash)에서 절대경로가 망가진다

Git Bash 가 `/app` 같은 인자를 Windows 경로로 변환한다. 실제로 겪은 출력:

```
$ docker compose exec -T backend df -h /app
df: 'C:/Program Files/Git/app': No such file or directory
```

두 가지 우회 모두 확인했다.

```bash
MSYS_NO_PATHCONV=1 docker compose exec -T backend df -h /app   # ✅
docker compose exec -T backend df -h                           # ✅ (인자를 주지 않는다)
```

PowerShell 에서는 이 문제가 없다.

---

## 1. DB 연결 실패 ✅ 검증

### 증상
- API 요청이 500, 로그에 `asyncpg` 연결 예외
- `docker compose exec backend uv run alembic current` 가 멈추거나 실패
- 통합테스트가 집단으로 실패

### 확인 명령

```bash
# ① 컨테이너가 떠 있고 healthy 한가
docker compose ps --format "table {{.Service}}\t{{.Status}}"

# ② DB 가 접속을 받는가 (가장 빠른 판별)
docker compose exec -T backend pg_isready -h db -U champs -d champs
```

확인한 출력과 종료 코드:

| 상황 | 출력 | exit |
|---|---|---|
| 정상 | `db:5432 - accepting connections` | `0` |
| 호스트 해석 실패 | `nope:5432 - no response` | `2` |

```bash
# ③ 실제 쿼리까지 가는가 (권한·DB명 문제를 여기서 가른다)
docker compose exec -T backend sh -c \
  'PGPASSWORD=devpassword psql -h db -U champs -d champs -c "select 1"'

# ④ DB 로그
docker compose logs --tail 50 db
```

③의 실패 메시지로 원인이 갈린다 (확인한 실제 문자열):

```
psql: error: could not translate host name "nope" to address: Name or service not known
   → 호스트명/네트워크 문제. DATABASE_URL 의 host 를 본다 (컨테이너 간에는 `db`, 호스트에서는 `localhost`)
```

### 1차 조치

```bash
# 컨테이너가 죽었으면
docker compose up -d db
# healthy 를 기다렸다가
docker compose ps
```

- `.env` 의 `DATABASE_URL` 호스트가 `db` 인지 확인한다. 호스트에서 직접 붙을 때만 `localhost:5432` 다.
- 볼륨이 꼬인 것 같으면 **`docker compose down -v` 는 마지막 수단이다** — `pgdata` 볼륨이 지워져 시드를 다시 적재해야 한다.

### 에스컬레이션 기준
- `pg_isready` 가 `accepting connections` 인데 애플리케이션만 못 붙으면 → 자격증명·DB명 문제. `.env` 와 compose 환경변수 비교
- 컨테이너가 반복 재시작하면 → **5. 컨테이너 재시작 반복**
- 디스크가 원인으로 보이면 → **4. 디스크 부족** (실제로 호스트가 98% 찬 상태를 확인했다)

---

## 1-B. DB 권한 오류 — 붙기는 하는데 거부된다 ✅ 검증

계정이 셋으로 분리돼 있다([ADR-015](adr/015-least-privilege-db-accounts.md)).
연결은 되는데 특정 작업만 실패하면 거의 항상 **계정을 잘못 골랐거나 역할이 없는** 경우다.

| 계정 | 할 수 있는 것 | 쓰는 곳 |
|---|---|---|
| `champs_migrate` | DDL (CREATE/ALTER/DROP) + 객체 소유 | `alembic`, `restore.sh` |
| `champs_app` | SELECT / INSERT / UPDATE / DELETE | 앱, 시드 적재, 수집, 정리 |
| `champs_read` | SELECT 만 (`pg_read_all_data`) | 디버깅·분석, `backup.sh` |
| `champs` | 슈퍼유저 | `init_roles.sh` 와 긴급 상황 **전용** |

### 증상과 원인 (실제로 확인한 메시지)

| 메시지 | 뜻 | 조치 |
|---|---|---|
| `permission denied for schema public` | 앱/읽기 계정으로 **DDL** 을 시도했다 | `champs_migrate` 로 바꾼다. 앱 코드라면 DDL 을 하려는 것 자체가 버그다 |
| `must be owner of table <t>` | 소유자가 아닌 계정으로 DROP/ALTER 를 시도했다 | 소유권이 `champs_migrate` 인지 본다 (아래 ②) |
| `permission denied for table <t>` | 읽기 계정으로 쓰기를, 또는 **기본 권한이 빠진 새 테이블**에 접근했다 | 아래 ③을 본다 |
| `FATAL: password authentication failed for user "champs_app"` | 역할이 없거나 비밀번호가 다르다 | `init_roles.sh` 를 돌린다 (아래 1차 조치) |
| `FATAL: role "champs_app" does not exist` | 역할을 만들지 않았다 | 같음 |

### 확인 명령

```bash
# ① 지금 어떤 계정으로 붙어 있나 / 역할이 존재하나
docker compose exec -T backend sh -c \
  'PGPASSWORD=devread psql -h db -U champs_read -d champs \
   -c "SELECT rolname, rolsuper, rolcreaterole FROM pg_roles WHERE rolname LIKE '"'"'champs%'"'"' ORDER BY 1"'
```

확인한 출력:

```
    rolname     | rolsuper | rolcreaterole
----------------+----------+---------------
 champs         | t        | t              ← 부트스트랩. 애플리케이션이 이걸 쓰면 안 된다
 champs_app     | f        | f
 champs_migrate | f        | f
 champs_read    | f        | f
```

```bash
# ② 테이블 소유자와 실제 ACL
docker compose exec -T backend sh -c \
  'PGPASSWORD=devread psql -h db -U champs_read -d champs \
   -c "SELECT relname, pg_get_userbyid(relowner) AS owner, relacl::text FROM pg_class \
       WHERE relname IN ('"'"'pokemon'"'"','"'"'meta_snapshots'"'"') AND relkind='"'"'r'"'"'"'
```

확인한 출력:

```
    relname     |     owner      |                                 relacl
----------------+----------------+------------------------------------------------------------------------
 meta_snapshots | champs_migrate | {champs_migrate=arwdDxt/champs_migrate,champs_app=arwd/champs_migrate}
 pokemon        | champs_migrate | {champs_migrate=arwdDxt/champs_migrate,champs_app=arwd/champs_migrate}
```

ACL 문자 읽는 법: `a`=INSERT, `r`=SELECT, `w`=UPDATE, `d`=DELETE, `D`=TRUNCATE,
`x`=REFERENCES, `t`=TRIGGER. **`champs_app` 에 `arwd` 만 있고 `D`(TRUNCATE)가 없는 것이
정상이다.** `champs_read` 는 여기 안 나온다 — 직접 GRANT 가 아니라 `pg_read_all_data`
역할 멤버십으로 읽기 때문이다. 그래서 `information_schema.role_table_grants` 로는
보이지 않는다 (실제로 0건이 나온다).

```bash
# ③ ★ 미래 객체 기본 권한 — 새 테이블에서 권한 오류가 날 때 여기를 본다
docker compose exec -T backend sh -c \
  'PGPASSWORD=devread psql -h db -U champs_read -d champs \
   -c "SELECT pg_get_userbyid(defaclrole) AS creator, defaclobjtype AS objtype, defaclacl::text \
       FROM pg_default_acl WHERE defaclnamespace='"'"'public'"'"'::regnamespace"'
```

확인한 출력:

```
    creator     | objtype |            defaclacl
----------------+---------+----------------------------------
 champs_migrate | r       | {champs_app=arwd/champs_migrate}
 champs_migrate | S       | {champs_app=rU/champs_migrate}
```

**`creator` 가 `champs_migrate` 여야 한다.** 다른 역할이거나 0건이면,
`ALTER DEFAULT PRIVILEGES` 에서 `FOR ROLE champs_migrate` 가 빠진 것이다.
그 경우 **마이그레이션이 새로 만든 테이블만** 앱이 못 읽고, 기존 테이블은 정상이라
증상이 테이블마다 다르게 나타난다.

### 1차 조치

```bash
# 역할 생성·권한 재적용. 멱등하다 — 몇 번 돌려도 된다.
docker compose exec backend bash scripts/init_roles.sh
```

`.env` 에 `CHAMPS_MIGRATE_PASSWORD` / `CHAMPS_APP_PASSWORD` / `CHAMPS_READ_PASSWORD`
가 있어야 한다. 없으면 스크립트가 **덤프를 만들기 전에** 멈추고 어느 변수가 비었는지 알려준다.

그 다음 검증:

```bash
docker compose exec backend uv run pytest tests/integration/test_db_privileges.py
```

21개가 전부 통과해야 한다. 특히 `TestRuntimeConfiguration` 은 **`.env` 의
`DATABASE_URL` 이 부트스트랩 계정으로 되돌아갔는지**를 잡는다 — 계정은 그대로 있으니
다른 테스트는 다 통과하는데 애플리케이션만 슈퍼유저로 도는 상태다
(실제로 겪었다, JOURNAL 2026-11-17).

### 에스컬레이션 기준
- `init_roles.sh` 를 돌려도 같은 오류면 → 소유권 문제일 수 있다. ②로 소유자를 확인한다
- 앱 코드가 DDL 을 하려 한다면 → **권한을 넓히지 않는다.** 그 코드가 잘못된 것이다.
  스키마 변경은 alembic 마이그레이션으로만 한다
- 새 테이블에서만 권한 오류가 나면 → ③. 기본 권한 누락이다

---

## 2. 백업 실패 / 복구 필요 ✅ 검증

### 증상
- `backup.sh` 가 0 이 아닌 코드로 끝난다
- 보존 디렉토리에 덤프가 없거나 `.partial` 만 있다
- 복구가 필요한 상황

### 확인 명령

```bash
# 백업 실행 (성공하면 생성된 경로를 표준출력 한 줄로 준다)
# ★ 계정을 넘기지 않는다 — 스크립트가 읽기 전용 계정(champs_read)을 기본값으로 쓴다
docker compose exec backend bash scripts/backup.sh

# 덤프가 읽히는 아카이브인가 (backup.sh 가 이미 이 검사를 통과시킨다)
docker compose exec backend pg_restore --list <덤프경로> | head

# DB 크기로 덤프 크기를 가늠한다
docker compose exec -T backend sh -c \
  'PGPASSWORD=devread psql -h db -U champs_read -d champs -At \
   -c "SELECT pg_size_pretty(pg_database_size(current_database()))"'
```

확인한 실측값 (2026-11-17): 개발 DB **13 MB**, 개발 DB 덤프 **192,193 bytes**,
리허설 DB 덤프 **20,840 bytes**. 덤프가 DB 크기보다 훨씬 작은 것이 정상이다 —
인덱스와 빈 공간이 덤프에 들어가지 않는다.

### 1차 조치

`backup.sh` 는 세 관문을 통과해야 최종 파일명을 준다. 실패 메시지로 어디서 막혔는지 안다.

| 로그 | 원인 | 조치 |
|---|---|---|
| `pg_dump 가 실패했다` | 접속·권한 | **1. DB 연결 실패** 로 |
| `덤프가 비어 있다` | pg_dump 가 0 으로 끝났는데 결과가 없음 | 디스크·권한 확인 |
| `덤프 목차를 읽을 수 없다` | 중간에 끊긴 아카이브 | 디스크 여유 확인 후 재실행 |
| `BACKUP_RETENTION_DAYS 는 0 이상의 정수여야 한다` | 설정 오타 | `.env` 수정 |

복구:

```bash
# ★ 계정을 넘기지 않는다 — 스크립트가 소유자 계정(champs_migrate)을 기본값으로 쓴다.
#   champs_app 으로는 --clean 이 기존 객체를 지울 수 없어 복구가 중간에 멈춘다.
docker compose exec backend bash scripts/restore.sh /app/backups/champs-<타임스탬프>Z.dump
```

Git Bash 에서는 경로 인자가 변환된다. `MSYS_NO_PATHCONV=1` 을 앞에 붙인다 (0장).

`restore.sh` 는 `--single-transaction` 이라 **전부 아니면 전무**다. 실패하면 기존
상태가 그대로 남는다. 손상된 덤프는 객체를 지우기 전에 거부한다.

### ★ 이 백업에 들어 있지 않은 것

`pg_dump` 는 데이터베이스 하나만 덤프한다. **ROLE(계정)과 클러스터 전역 권한은
들어 있지 않다.** 빈 서버에 복구할 때는 계정을 먼저 만들어야 한다.
`pg_dumpall --roles-only` 가 별도로 필요하고, **아직 없다.**
지금은 `backend/sql/roles.sql` 이 그 역할을 대신한다 — 역할 정의가 저장소에
있으므로 `init_roles.sh` 로 재현할 수 있다. 다만 운영에서 손으로 바꾼 권한은 유실된다
([ADR-015](adr/015-least-privilege-db-accounts.md) 의 `포기한 것`).

### 복구가 되는지 어떻게 아는가

추측하지 않는다. 리허설이 있다.

```bash
docker compose --profile rehearsal up -d db-rehearsal
docker compose exec backend uv run pytest -m rehearsal
```

백업 → 전체 테이블 DROP → 복구 → 행 수·체크섬·제약·시퀀스 4층 검증을 실제로 수행한다
([ADR-014](adr/014-restore-rehearsal.md)). 2026-11-17 기준 **29 passed**.

### 에스컬레이션 기준
- 리허설이 깨지면 **백업을 믿을 수 없는 상태다.** 그 상태로 운영 배포를 진행하지 않는다
- 복구 후 애플리케이션이 쓰기에서만 실패하면 → 시퀀스 문제. 리허설 4층이 이걸 본다

---

## 3. 수집 배치 실패 ⚠️ 부분 검증

외부 API 응답 없음·스키마 변경은 **테스트로 재현한 이력이 있다**(픽스처 변조, 게이트
RED 재현). 실제 운영 중 출처 장애는 겪지 않았다.

### 증상
- Airflow 작업이 빨갛다 (`--profile airflow`)
- 사용률 화면이 옛 데이터를 보여준다
- CLI 종료 코드가 0 이 아니다

### 확인 명령

```bash
# 수집 CLI 를 직접 돌려본다 (Airflow 없이도 돈다 — ADR-012)
docker compose exec backend uv run python -m scripts.run_ingest --format Singles --limit 5
echo "exit=$?"

# 활성 스냅샷이 언제 것인가
docker compose exec -T backend sh -c \
  'PGPASSWORD=devpassword psql -h db -U champs -d champs \
   -c "SELECT id, regulation_id, format, collected_at, is_active \
       FROM meta_snapshots WHERE is_active ORDER BY id"'
```

확인한 출력 형태 (2026-11-17):

```
 id  | regulation_id | format  |         collected_at          | is_active
-----+---------------+---------+-------------------------------+-----------
 263 |           156 | Singles | 2026-11-14 12:56:03.505004+00 | t
```

### 종료 코드로 원인이 갈린다

| exit | 상수 | 뜻 | 1차 조치 |
|---|---|---|---|
| `0` | `EXIT_OK` | 수집·검증·활성화 성공 | — |
| `1` | `EXIT_COLLECTION_FAILED` | **수집 자체 실패** (API 응답 없음·HTTP 오류) | 출처 상태 확인 후 재시도. 활성 스냅샷은 그대로 유지된다 |
| `2` | `EXIT_NOT_ACTIVATED` | 적재는 됐지만 **검증 실패로 활성화를 막았다** | 매핑 누락을 확인한다. 이건 설계된 동작이다 |

**`2` 는 고장이 아니다.** Phase 2-5 의 설계가 "검증 실패 시 적재는 하고 활성화는
막는다"이고, 그래야 이전 스냅샷으로 계속 서비스하면서 원본을 보고 매핑을 고칠 수 있다.
틀린 이름이 섞인 새 스냅샷을 쓰는 것보다 낫다.

### 1차 조치
- `1` → 출처 장애일 수 있다. **몰아치지 않는다** (요청 간 1초, 동시성 1 — [ADR-013](adr/013-attribution-contract.md)). 활성 스냅샷이 남아 있으면 서비스는 정상이다
- `2` → `name_map_pending.json` 에 적재된 미매핑 원문을 본다. **추측으로 채우지 않는다**([ADR-005](adr/005-name-mapping-defense.md))
- 계산 기능은 어느 경우에도 정상이어야 한다 (CLAUDE.md 1-4). 계산까지 죽었으면 수집 문제가 아니다

### 에스컬레이션 기준
- 활성 스냅샷의 `collected_at` 이 보존 기간에 가까워지면 → 데이터가 만료된다. 출처 복구를 기다릴지 수동 수집할지 판단
- 같은 미매핑이 반복되면 → 출처가 명칭 체계를 바꿨을 수 있다. 매핑 추가가 아니라 ADR-005 재검토

---

## 4. 디스크 부족 ⚠️ 부분 검증

확인 명령은 실행했다. **실제 고갈 상황은 재현하지 않았다.**

### 증상 (설계상 예상)
- `backup.sh` 가 `덤프 목차를 읽을 수 없다` 로 실패 (중간에 끊긴 아카이브)
- Postgres 가 쓰기를 거부
- 컨테이너가 시작 실패

### 확인 명령

```bash
# 컨테이너 안에서 (인자 없이 — Git Bash 경로 변환 회피)
docker compose exec -T backend df -h

# 특정 경로
MSYS_NO_PATHCONV=1 docker compose exec -T backend df -h /app

# 도커가 먹고 있는 용량
docker system df
```

**실측 (2026-11-17)**: 호스트 `C:\` 가 **476G 중 14G 남음 (98% 사용)**.
컨테이너 overlay 는 1007G 중 33G(4%)로 여유가 있지만, `/app` 은 호스트 바인드 마운트라
호스트 디스크를 쓴다. **지금 상태가 이미 경계에 가깝다.**

### 1차 조치

```bash
# 보존 기간 지난 백업 정리 — backup.sh 가 실행마다 자동으로 한다
docker compose exec backend sh -c 'ls -lh /app/backups | tail'

# 보존 기간 지난 스냅샷 정리 (Phase 2-6 CLI)
docker compose exec backend uv run python -m scripts.cleanup_snapshots

# 도커 잔여물
docker system prune -f        # ★ 볼륨은 지우지 않는다
```

`docker system prune --volumes` 는 쓰지 않는다 — `pgdata` 가 날아간다.

### 에스컬레이션 기준
- 정리 후에도 여유가 10% 미만이면 → 호스트 차원 정리가 필요하다. 이 런북 범위 밖
- `backup.sh` 가 디스크 때문에 반복 실패하면 → 백업 없는 상태다. 최우선으로 처리

---

## 5. 컨테이너 재시작 반복 ⚠️ 부분 검증 (명령만)

확인 명령은 실행해서 동작을 봤다. **재시작 반복 상황 자체는 재현하지 않았다.**

### 확인 명령

```bash
docker compose ps --format "table {{.Service}}\t{{.Status}}"

# 재시작 횟수와 헬스 상태를 한 줄로
docker inspect --format \
  '{{.Name}} restarts={{.RestartCount}} health={{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' \
  $(docker compose ps -q)

# 죽기 직전 로그 (--tail 없이 보면 길다)
docker compose logs --tail 100 <서비스>
```

확인한 출력 (정상 상태, 2026-11-17):

```
/pokehelper-backend-1 restarts=0 health=healthy
/pokehelper-db-1 restarts=0 health=healthy
/pokehelper-db-rehearsal-1 restarts=0 health=healthy
/pokehelper-redis-1 restarts=0 health=healthy
```

`restarts` 가 0 이 아니고 계속 올라가면 재시작 루프다.

### 1차 조치 (설계상 — 미검증)
- `depends_on` 의 `condition: service_healthy` 때문에 **의존 대상이 healthy 가 아니면 기다린다.** 먼저 `db`·`redis` 상태를 본다
- backend 가 import 오류로 죽으면 로그 첫 줄에 트레이스백이 나온다 — 코드 문제이므로 재시작으로 해결되지 않는다
- 바인드 마운트가 컨테이너 파일을 가려서 생기는 문제를 두 번 겪었다 (JOURNAL 2026-11-03, 2026-11-17). `docker compose exec <서비스> ls <경로>` 로 **컨테이너 안에서** 확인한다

### 에스컬레이션 기준
- 3회 이상 재시작하고 로그가 매번 같으면 → 환경 문제가 아니라 코드/설정 문제
- OOM 이 의심되면 → compose 에 메모리 제한이 없으므로 호스트 메모리를 본다. Airflow 프로필이 약 930MB 를 더 쓴다([ADR-012](adr/012-airflow-docker-operator.md))

---

## 6. API 응답 지연 ❌ 미검증

**재현 수단이 없다.** 지연을 측정할 관측 스택(메트릭·히스토그램)이 아직 없어서,
"p95 가 올랐다"를 판별할 방법이 이 저장소에 존재하지 않는다.

지금 할 수 있는 것은 단건 측정뿐이다 (명령 자체는 동작을 확인했다):

```bash
docker compose exec -T backend python -c "import urllib.request,time; \
t=time.monotonic(); urllib.request.urlopen('http://localhost:8000/health').read(); \
print(f'{(time.monotonic()-t)*1000:.1f} ms')"
```

채워 넣으려면 필요한 것:
- 요청률·에러율·응답시간 히스토그램 (`/metrics`)
- 느린 쿼리 식별 (`pg_stat_statements` 는 아직 활성화하지 않았다)
- 임계치와 그 근거

관측 스택을 세우기 전까지 이 항목은 **비워 둔다.** 추측으로 채우면 장애 때 한 번 더
시간을 쓰게 된다.
