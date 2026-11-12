# scripts — 시드 생성기와 운영 커맨드

여기 있는 스크립트는 **성격이 둘로 갈린다.** 섞어서 생각하면 사고가 난다.

| 구분 | 언제 돌리나 | 외부 호출 | 예시 |
|---|---|---|---|
| **시드 생성기** | 사람이 가끔 (패치·시즌 전환 때) | op.gg, PokéAPI 등 | `generate_moves`, `generate_name_map` |
| **운영 커맨드** | 주기적으로 (스케줄러) | championsbattledata API | `run_ingest`, `cleanup_snapshots` |

시드 생성기는 **결과를 커밋한다.** 산출물이 `data/*.json`이고 그게 진실의 원천이다
(ADR-010). 운영 커맨드는 **DB를 바꾼다.** 커밋할 산출물이 없다.

모든 실행은 컨테이너 안에서 한다 (`CLAUDE.md` 6장).

---

## 운영 커맨드

### 수집

```bash
docker compose exec backend uv run python -m scripts.run_ingest
docker compose exec backend uv run python -m scripts.run_ingest --format Singles --limit 50
```

요청 간 1초·동시성 1이라 `--limit 50`이면 약 50초 걸린다.

**종료 코드를 반드시 확인한다.** 스케줄러가 보는 유일한 신호다.

| 코드 | 뜻 | 해야 할 일 |
|---|---|---|
| `0` | 활성 스냅샷이 갱신됐다 | 없음 |
| `1` | 수집 자체가 실패했다 | 네트워크·출처 확인. 이전 스냅샷으로 계속 서비스 중 |
| `2` | **적재는 됐지만 활성화 못 했다** | 해석 실패 내역을 보고 매핑을 고친 뒤 재실행 |

`2`가 가장 위험하다. 데이터는 DB에 있지만 **서비스가 보는 메타는 그대로**다.
성공으로 처리하면 매핑이 깨진 걸 며칠간 아무도 모른다 (ADR-011).

### 정리

```bash
docker compose exec backend uv run python -m scripts.cleanup_snapshots --dry-run
docker compose exec backend uv run python -m scripts.cleanup_snapshots
```

보존 기한(`expires_at`, 기본 14일)이 지난 **비활성** 스냅샷을 지운다.
무한 누적은 약관 위반이다 (`CLAUDE.md` 8-2).

**활성 스냅샷은 만료돼도 지우지 않는다.** 지우면 서비스가 볼 메타가 사라진다.
수집이 며칠 멈춰도 마지막 스냅샷으로 서비스해야 한다 (`CLAUDE.md` 8-5).

### 실행 순서

```
run_ingest  →  cleanup_snapshots
```

수집이 성공해야 정리를 돌린다. 수집이 실패한 날 정리부터 돌리면, 새 스냅샷은
못 받았는데 오래된 것만 지워져 보존 기간이 짧아진다.

---

## 시드 생성기

의존 순서가 있다. **위에서 아래로** 돌린다.

```
generate_typechart      타입 상성 (PokéAPI)
generate_natures        성격 25 (PokéAPI)
generate_pokemon        포켓몬 263 (championsbattledata)
generate_moves          기술 516 (op.gg) ← learnset 으로 누락을 보완한다
generate_learnsets      learnset 16,037쌍 ← moves.json 이 먼저 있어야 한다
generate_name_map       포켓몬 한영 매핑 ← pokemon.json 이 먼저
generate_name_map_extras 성격·특성 한영 매핑 ← name_map.json 이 먼저
```

생성기는 전부 **검증을 통과해야만 파일을 쓴다.** 중간에 멈추면 기존 파일은 그대로다.

마지막에 DB로 옮긴다:

```bash
docker compose exec backend uv run python -m scripts.load_seed_to_db
```

멱등하다. 몇 번을 돌려도 같다. 시드에서 사라진 행은 **지우지 않고 보고만 한다** —
수집 데이터가 FK로 참조 중이면 같이 날아간다.

### 응답 캐시

`backend/.cache/pokeapi/`에 외부 응답을 저장한다 (커밋 안 함).
검수→재생성을 반복할 때 같은 응답을 다시 받지 않기 위한 것이다.
**출처를 새로 확인하고 싶으면 이 디렉터리를 지우고 돌린다.**

---

## 나중에 Airflow가 붙으면

DAG는 위 운영 커맨드를 호출하기만 한다. 수집 로직은 DAG 안에 넣지 않는다 —
그러면 Airflow 없이는 수집을 못 돌리게 된다 (ADR-011).

```python
ingest = BashOperator(bash_command="python -m scripts.run_ingest --limit 50", retries=2)
cleanup = BashOperator(bash_command="python -m scripts.cleanup_snapshots")
ingest >> cleanup
```

`catchup=False`를 반드시 켠다. 스냅샷은 14일만 보존하고 과거 데이터를 다시 받을 수도
없어서, 소급 실행은 의미가 없고 요청만 늘린다.
