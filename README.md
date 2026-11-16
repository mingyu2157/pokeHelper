# pokeHelper

**정답이 존재하는 결정론적 계산 도메인에서, LLM 을 신뢰하지 않도록 설계한 서비스.**

LLM 에게 "이 공격이 몇 % 들어가?"를 물으면 그럴듯한 숫자가 나온다. 그리고 그 숫자는
검증할 방법이 없다. 사용자가 그걸 믿고 판단을 내리면, 서비스는 틀린 확신을 판 셈이 된다.

이 프로젝트는 그 문제를 **구조로** 막았다.

```
        ┌─ 결정론적 계산 영역 ─────────────┐   ┌─ LLM 영역 ──────────┐
        │                                  │   │                     │
입력 ──▶│  순수 함수 계산 엔진              │──▶│  수치 해석 · 설명    │──▶ 응답
        │  외부 의존성 0                    │   │  숫자 생성 금지      │
        │  테스트 509개로 검증              │   │  스키마로만 통제     │
        └──────────────────────────────────┘   └─────────────────────┘
             모든 수치가 여기서만 나온다           여기서 나온 숫자는 버린다
```

사용자가 의존하는 것은 **수치**다. 수치를 전부 테스트 가능한 쪽으로 밀어내면,
검증할 수 없는 영역(LLM)과 검증해야 하는 영역(계산)이 분리된다. 그 경계를 지키는 것이
이 저장소의 설계 제약 1번이다.

## 이 프로젝트가 다룬 엔지니어링 문제

| 문제 | 대응 | 기록 |
|---|---|---|
| **공식 명세가 없는 도메인을 어떻게 테스트하나** | 오라클을 4종으로 나누고, 불확실한 영역은 메타모픽 관계로 검증. 미확정 결정은 `잠정 채택` 상태로 유지 | [TEST_STRATEGY §4](docs/TEST_STRATEGY.md#4-테스트-오라클-문제와-해결-) · [ADR-003](docs/adr/003-damage-chain.md) |
| **외부 데이터 출처가 깨져도 핵심 기능이 살아야 한다** | 계산 엔진은 외부 의존성 0. 수집 실패 시 이전 스냅샷으로 서비스 계속 | [ADR-010](docs/adr/010-seed-source-of-truth.md) |
| **이름 매핑 오류는 조용히 틀린 화면을 만든다** | 퍼지 매칭 전면 금지, 7단계 방어선, 매핑 실패 시 fail-closed | [ADR-005](docs/adr/005-name-mapping-defense.md) |
| **본가 데이터를 재사용하면 조용히 틀린다** | 기술 위력·명중·PP 를 챔피언스 전용 출처에서 재생성, 3출처 교차검증 | [ADR-009](docs/adr/009-move-data-source.md) |
| **스케줄러 도입이 되돌리기 비싼 결정이 되는 것** | CLI 를 먼저 만들고 Airflow 가 그걸 호출. 의존성을 컨테이너로 격리 | [ADR-011](docs/adr/011-scheduler-staging.md) · [ADR-012](docs/adr/012-airflow-docker-operator.md) |

## 먼저 볼 문서

| 문서 | 내용 |
|---|---|
| **[docs/TEST_STRATEGY.md](docs/TEST_STRATEGY.md)** | 테스트 레벨·유형·설계 기법 매핑, **테스트 오라클 문제와 해결** |
| **[docs/adr/](docs/adr/)** | 설계 결정 기록 12건. 선택지·근거·포기한 것·**되돌리는 조건** |
| **[docs/decisions/JOURNAL.md](docs/decisions/JOURNAL.md)** | 문제 해결 일지 27건. 증상·기대 동작·재현 절차·원인·해결 |
| [CLAUDE.md](CLAUDE.md) | 이 저장소의 작업 규칙과 계산 명세 (진실의 원천) |

ADR 은 결정으로 끝내지 않고 **되돌리는 조건**을 미리 적어둔다. 예를 들어 ADR-012 는
"AWS 로 올라갈 때 반드시 재검토한다 — ECS 에서 docker.sock 마운트는 불가능하거나 매우
위험하다"를 결정과 같은 커밋에 적었다. 판단이 틀렸을 때 그걸 알아차릴 조건을 함께 남기는
것이 이 기록물의 목적이다.

---

## 무엇을 하는 서비스인가

**포켓몬 챔피언스(Pokémon Champions) 실전배틀용 파티 구축 · 선출 판단 보조 도구.**

대상 사용자는 두 부류다.

- 인터넷에서 파티를 복사해 쓰지만 **왜 그렇게 조정하는지 모르는** 초보
- 선출 판단을 **빠르게 검증하고 싶은** 중상급자

따라서 모든 출력에 **근거(수치 · 출처)가 붙는다.** 근거 없는 추천은 이 서비스의
존재 이유를 부정한다.

### 계산하는 것 / 수집하는 것

이 구분이 아키텍처의 중심이다.

| 우리가 계산한다 (결정론적) | 외부에서 수집한다 |
|---|---|
| 실수치, 스피드 라인 | 포켓몬 사용 순위 · 사용률 |
| 데미지 % (난수 16단계 전부) | 아이템 · 성격 · 특성 · 기술 채용률 |
| 확정 n타 / 난수 n타 + 확률 | 노력치 배분 채용률 |
| 타입 상성 판정 | 파트너(파티 동반) 채용률 |
| 노력치 미세조정 해 탐색 | 종족값 · 타입 · 특성 목록 |

외부에서 가져오는 것은 **"사람들이 무엇을 고르는가"** 뿐이다. 수치는 전부 우리가 만든다.
수집이 깨져도 계산 기능은 100% 동작해야 한다는 요구사항에서 나온 경계다.

### 챔피언스는 본가와 규칙이 다르다

본가 공식을 그대로 쓰면 틀린다. 확정된 차이:

- **Lv.50 고정, 개체값 31 고정** → 공식이 축약된다: `HP = 종족값 + 75 + 포인트`,
  `기타 = floor((종족값 + 20 + 포인트) × 성격보정)`
- **노력치가 포인트 방식** — 스탯당 0\~32, 총합 66. 기존 시리즈의 EV(0\~252) 개념이
  이 프로젝트에 **존재하지 않는다.** 변환 함수도 만들지 않았다.
- **개체값 0 조정이 불가능** → 저속 조정의 하한이 "하락보정 + 0포인트"다.
  트릭룸 설계에서 본가 감각을 가져오면 안 된다.
- **기술 위력 · 명중이 패치로 조정**되고 PP 가 `{1, 8, 12, 16, 20}` 로 정규화됐다.
- **포인트와 실수치가 선형이 아니다** — 상승보정에서 1포인트가 실수치를 2 올리는
  지점이 있고(매직넘버), 하락보정에서는 올려도 그대로인 구간이 있다.
  그래서 노력치 솔버의 판정 기준은 포인트가 아니라 **항상 최종 실수치**다.

마지막 항목은 이진 탐색을 쓰면 **예외가 아니라 "1포인트 적은 잘못된 배분"이 조용히
나온다.** 그래서 선형 스캔을 택했다 ([ADR-008](docs/adr/008-solver-search-strategy.md)).

---

## 기술 스택

| 레이어 | 선택 |
|---|---|
| 백엔드 | Python 3.12 · FastAPI · Pydantic v2 · uv |
| DB | PostgreSQL 16 (JSONB 하이브리드) · SQLAlchemy 2.x async · Alembic |
| 캐시 | Redis 7 |
| 파이프라인 | Airflow 3.4 (DockerOperator 격리 실행) |
| 프론트 | Next.js 16 (App Router) · TypeScript · Tailwind · shadcn/ui |
| LLM | Anthropic Claude API (백엔드에서만 호출) |
| 품질 | pytest · ruff · mypy --strict (domain 한정) · GitHub Actions |
| 인프라 | Docker Compose → AWS ECS Fargate · Terraform (Phase 6) |

선정 근거는 [ADR-001](docs/adr/001-tech-stack.md), [ADR-004](docs/adr/004-postgres-jsonb.md),
[ADR-006](docs/adr/006-data-pipeline-stack.md) 에 있다.

## 품질 현황

2026-11-17 측정.

```
테스트 함수      509   (단위 415 / 통합 93 / 시스템 1)
마지막 전체 실행  615 passed   (파라미터화 전개 후, 2026-11-17)
린트 · 포맷      ruff — 저장소 전체
타입체크         mypy --strict — app/domain/
CI               모든 PR 에서 lint → format → mypy → 마이그레이션 → 시드 → pytest
```

CI 의 DB 통합 테스트는 **실제 Postgres 16 컨테이너**를 띄운다. SQLite 로 대체하지 않는다
— 이 스키마의 핵심 장치가 부분 유니크 인덱스(`WHERE is_active`)와 CHECK 제약인데
방언마다 동작이 다르거나 아예 없다. 상세는
[TEST_STRATEGY §1-2](docs/TEST_STRATEGY.md#1-2-통합--두-종류를-구분한다).

**아직 없는 것**: 커버리지 CI 게이트, 골든 데미지 케이스(실측 필요), E2E, LLM 응답 검증,
수집 샘플 응답의 실측 검증.
[TEST_STRATEGY §6](docs/TEST_STRATEGY.md#6-현재-한계) 에 전부 적어뒀다.

---

## 로드맵

진행 중인 프로젝트다. 미완성을 숨기지 않고 현재 지점을 적는다.

| | Phase | 내용 | 상태 |
|---|---|---|---|
| ✅ | **0** | 저장소 · Docker Compose · CI 뼈대 | 완료 |
| ✅ | **1** | **도메인 계산 엔진 + 테스트** — 실수치 · 타입 상성 · 데미지 · 스피드 · 노력치 솔버 | 완료 |
| 🔄 | **2** | 데이터 수집 + DB | **진행 중 — 아래 참조** |
| ⬜ | 3 | 선출 추천 API + 최소 프론트 | |
| ⬜ | 4 | LLM 설명 레이어 | |
| ⬜ | 5 | 파티 빌더 | |
| ⬜ | 6 | AWS 배포 (ECS Fargate · Terraform) | |

### Phase 1 산출물 (완료)

`app/domain/` — 외부 의존성 0, 순수 함수. 단위 테스트 384개.

실수치 · 타입 상성 · 데미지(난수 16단계) · 확정타 판정 · 스피드 라인 · 행동 순서 ·
노력치 검증 · 노력치 미세조정 솔버(제약 충돌 보고 포함) · 결정력/내구력 보조 지표.

> 골든 데미지 케이스는 게임 실측이 필요해 **별도 트랙으로 남아 있다.**
> [ADR-003](docs/adr/003-damage-chain.md) 은 아직 `잠정 채택` 이다.

### Phase 2 진행 상황

| | 단계 | 내용 |
|---|---|---|
| ✅ | 2-1 | 마스터 시드 — 포켓몬 263종 · 타입 상성 · 성격 25종 |
| ✅ | 2-2 | 기술 시드 516개 — op.gg 챔피언스 표에서 생성, 3출처 교차검증 ([ADR-009](docs/adr/009-move-data-source.md)) |
| ✅ | 2-3 | 한영 명칭 매핑 — 포켓몬 263 · 성격 25 · 특성 202 ([ADR-005](docs/adr/005-name-mapping-defense.md)) |
| ✅ | 2-4 | DB 스키마 + Alembic — 테이블 9개, 활성 스냅샷 유일성을 부분 유니크 인덱스로 보장 |
| ✅ | 2-5 | ETL + 스냅샷 전환 — **검증 실패 시 적재는 하고 활성화를 막는다** |
| ✅ | 2-6 | learnset 16,037쌍 · 보존기간 정리 CLI · Airflow ([ADR-011](docs/adr/011-scheduler-staging.md) · [ADR-012](docs/adr/012-airflow-docker-operator.md)) |
| 🔜 | **2-3c** | **다음 작업** — 도구 한영 매핑. 교차검증 재료가 없는 유일한 대상이라 분류별로 다른 재료를 쓴다 |
| ⬜ | 2-7 | 출처 표기 컴포넌트 (데이터 제공자 라이선스 조건) |

**2-5 의 설계가 Phase 2 의 핵심이다.** 수집은 깨질 수 있다 — 출처가 항목을 추가하거나
이름이 바뀌거나 매핑이 비어 있을 수 있다. 그때 **이전 스냅샷으로 계속 서비스하는 것**이
요구사항이고, 틀린 이름이 섞인 새 스냅샷을 쓰는 것보다 낫다. 그래서 적재는 하고
활성 전환만 막는다. 원본이 DB 에 남아 있어야 무엇이 왜 실패했는지 보고, 매핑을 고친 뒤
재수집 없이 다시 검증할 수 있다.

---

## 로컬 실행

실행과 테스트는 **항상 컨테이너 안에서** 한다 (호스트 버전 차이로 "내 컴에선 되는데"가
시작되지 않도록).

```bash
cp .env.example .env
docker compose up -d

# 스키마 + 시드
docker compose exec backend uv run alembic upgrade head
docker compose exec backend uv run python -m scripts.load_seed_to_db

# 품질 검사
docker compose exec backend uv run pytest
docker compose exec backend uv run ruff check .
docker compose exec backend uv run mypy app/domain/

# 사용률 수집 (스케줄러 없이도 돈다)
docker compose exec backend uv run python -m scripts.run_ingest --limit 50
```

Airflow 는 평소에 뜨지 않는다. 필요할 때만 profile 로 올린다 —
[infra/airflow/README.md](infra/airflow/README.md).

```bash
docker compose --profile airflow up -d     # http://localhost:8080
```

---

## 데이터 출처

- **배틀 데이터**: Battle data provided by Pokémon Champions Battle Data
  — 공개 API 사용. 영구 미러 · 경쟁 API · 대량 다운로드 제공 안 함
- **기술 수치**: op.gg 포켓몬 챔피언스 — 출처 표기 조건 하 비영리 개인 프로젝트 사용
  ([ADR-009](docs/adr/009-move-data-source.md))
- **보조 검증**: PokéAPI (한국어 명칭 교차검증)

공식 제휴가 아니며 포켓몬 관련 상표권을 주장하지 않는다.
라이선스 조건은 [CLAUDE.md 8-2](CLAUDE.md) 에 협상 불가 항목으로 명시돼 있다.
