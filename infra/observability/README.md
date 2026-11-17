# 관측성 스택

Prometheus + Grafana + 익스포터 셋. 설계 근거는
[ADR-016](../../docs/adr/016-observability-metrics.md).

## 띄우기

```bash
docker compose --profile observability up -d
```

평소에는 뜨지 않는다 (Airflow·복구 리허설과 같은 방식). 켜두면 컨테이너 5개가
상시 메모리를 쓰고, 개발 중 부하가 섞여서 **"원래 이렇게 느렸나"의 기준선이
흐려진다.**

| 주소 | 무엇 |
|---|---|
| http://localhost:9090 | Prometheus — 쿼리, 타깃 상태, 경보 |
| http://localhost:3001 | Grafana — 대시보드 (`admin` / `admin`) |
| http://localhost:8000/metrics | 백엔드 지표 원본 |

Grafana 포트가 3001 인 이유: 3000 은 프론트엔드가 쓴다.

## 구성

```
prometheus/
  prometheus.yml          스크랩 대상 4개
  rules/alerts.yml        경보 2개
grafana/
  provisioning/           데이터소스·대시보드 자동 등록
  dashboards/pokehelper.json
```

**UI 에서 만들지 않는다.** UI 설정은 Grafana 볼륨에만 남아 저장소에 기록되지
않고, 볼륨을 지우면 사라진다. 파일로 두면 `docker compose up` 만으로 재현된다.
`allowUiUpdates: false` 라 UI 에서 고친 대시보드는 파일로 되돌아간다.

## 스크랩 대상

| job | 대상 | 비고 |
|---|---|---|
| `backend` | `backend:8000/metrics` | 요청률·지연·오류율 + ETL 신선도 |
| `postgres` | `postgres-exporter:9187` | **`champs_read` 계정** (ADR-015) |
| `redis` | `redis-exporter:9121` | |
| `cadvisor` | `cadvisor:8080` | 컨테이너별 CPU·메모리 |

`node_exporter` 를 쓰지 않는다 — Docker Desktop 에서는 호스트가 아니라 리눅스
VM 의 수치를 보여줘서, "메모리 80%" 가 내 PC 인지 VM 인지 알 수 없다.

### 익스포터 계정과 `--no-collector.wal`

`postgres-exporter` 는 읽기 전용 계정으로 돈다. 그래서 `wal` 콜렉터가
`pg_ls_waldir()` 권한 오류를 내는데, **계정을 넓히는 대신 콜렉터를 껐다.**
WAL 지표를 대시보드에 쓰지 않으므로 쓰지도 않는 지표 때문에 읽기 계정에
`pg_monitor` 를 줄 이유가 없다. 복제를 구성하면 다시 본다 (ADR-016 되돌리는 조건).

기동 시 `Error loading config ... postgres_exporter.yml` WARN 한 줄이 나온다.
기본 설정파일 경로를 찾는 것이고 우리는 환경변수로 접속하므로 무해하다.
반복되지 않는다.

## 검증

```bash
# 설정·규칙 문법 (Git Bash 는 MSYS_NO_PATHCONV=1 필요)
MSYS_NO_PATHCONV=1 docker compose exec -T prometheus \
  promtool check config /etc/prometheus/prometheus.yml

# 스크랩 대상이 실제로 긁히는가 — "설정했다"와 "스크랩된다"는 다른 사실이다
curl -s 'http://localhost:9090/api/v1/targets?state=any' | python -c "
import json,sys
for t in json.load(sys.stdin)['data']['activeTargets']:
    print(t['labels']['job'], t['health'], t.get('lastError',''))"

# 경보 상태 (pending = 조건이 참 / firing = 경보가 나갔다)
curl -s 'http://localhost:9090/api/v1/alerts'
```

지표명 교차검증은 pytest 가 한다 — `tests/integration/test_alert_rules.py`.
경보가 **존재하지 않는 지표**를 참조하면 PromQL 은 오류가 아니라 빈 결과를
내므로, 규칙이 유효한 상태로 영원히 울리지 않는다. `promtool` 은 문법만 보기
때문에 이걸 잡지 못한다.

## 경보

| 경보 | 조건 | 대기 | 심각도 |
|---|---|---|---|
| `PokehelperEtlStale` | 활성 스냅샷이 30시간 넘게 안 갱신, **또는 0개** | 10분 | warning |
| `PokehelperTargetDown` | `up == 0` | 2분 | critical |

`or ... == 0` 절이 있는 이유: 타임스탬프 게이지만으로는 **"한 번도 수집되지
않았다"** 를 표현할 수 없다. 계열이 아예 없으면 `time() - metric > 30h` 가
평가되지 않아 조용히 지나간다.

**Alertmanager 가 없다.** 경보는 Prometheus UI 에만 뜨고 아무에게도 전달되지
않는다 (ADR-016 포기한 것).
