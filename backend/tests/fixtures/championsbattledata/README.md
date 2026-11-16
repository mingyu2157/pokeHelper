# championsbattledata 샘플 응답

이 디렉터리의 JSON 은 `app/ingestion/championsbattledata.py` 가 **가정하는 응답 형태**다.

## ★ 이것은 실측 응답이 아니다 ★

실제 API 를 호출해 저장한 것이 **아니다.** 수집기 코드가 읽는 필드
(`rankings`, `showdownId`, `position`, `season`, `seasons`, `rows`, `category`,
`rank`, `name`, `percentage_value`, `*_points`)에서 **역산해 만든 것**이다.

따라서 이 fixture 로 할 수 있는 것과 할 수 없는 것이 갈린다.

| 할 수 있다 | 할 수 없다 |
|---|---|
| 파서가 이 계약을 지키는지 고정한다 | **출처가 형태를 바꾼 것을 탐지한다** |
| 가정하는 응답 형태를 한 곳에 읽을 수 있게 둔다 | 실제 값의 정확성을 보장한다 |
| 네트워크 없이 수집 경로 전체를 돌린다 | 필드 누락·타입 변경을 알려준다 |

**오른쪽 열이 비어 있지 않다는 걸 숨기지 않는다.** 파서와 fixture 가 같은 가정에서
나왔으므로, 가정 자체가 틀렸으면 **둘 다 같이 틀리고 테스트는 통과한다.**
이 fixture 는 회귀를 막지만 오라클은 아니다.

## 실측으로 올리는 방법 (아직 하지 않았다)

실호출 1회로 승격할 수 있다. 그때 이 문서의 상태를 바꾼다.

```bash
# 요청 간 1초 / 동시성 1 을 지켜 2회만 호출한다 (CLAUDE.md 8-6)
docker compose exec backend python - <<'PY'
import httpx, json, pathlib
root = "https://championsbattledata.com/api"
ua = {"User-Agent": "pokehelper/0.1 (+https://github.com/mingyu2157/pokeHelper)"}
with httpx.Client(headers=ua, timeout=60) as c:
    r = c.get(f"{root}/rankings", params={"format": "Singles", "limit": 2})
    pathlib.Path("captured_rankings.json").write_text(r.text, encoding="utf-8")
PY
```

받은 응답을 이 디렉터리의 파일과 **구조만** 비교한다 (키 집합, 타입).
다르면 자동으로 고치지 않는다 — 차이 목록을 남기고 판단한다.
파서가 틀렸으면 파서를 고치고, fixture 가 틀렸으면 fixture 를 고친다.
어느 쪽인지 모른 채로 양쪽을 맞추면 이 fixture 의 의미가 사라진다.

## 라이선스

원본 데이터 출처: Pokémon Champions Battle Data (https://championsbattledata.com).
이 파일들은 형태 검증용 최소 샘플이며 원본의 미러가 아니다 (CLAUDE.md 8-2).
