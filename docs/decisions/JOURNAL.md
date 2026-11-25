# 결정 저널

가벼운 결정과 문제 해결 기록. append-only — 과거 항목을 고쳐 쓰지 않는다.
구조적 결정(되돌리기 비싼 것)은 여기가 아니라 `docs/adr/` 에 쓴다.

기록 기준은 `CLAUDE.md` 10장 참조.

---

## 2026-09-23 — Next.js 15 → 16 상향

- 상황: Phase 0 프론트 뼈대를 만들고 `npm audit` 을 돌리니 Next 15.1.6에 critical 포함
  30건 넘는 권고가 떴다 (RCE, 인증 우회 등).
- 1차 조치: `CLAUDE.md` 2장이 "Next.js 15"로 스택을 못 박고 있어 메이저를 유지하고
  15 라인의 백포트 최신판 15.5.25로 올렸다. critical은 사라졌다.
- 남은 문제: high 1건. Next 15가 내부에 번들한 `postcss 8.4.31`. 우리가 직접 쓰는 postcss는
  8.5.28로 이미 패치본이었고, 번들된 쪽은 **Next 16으로 올려야만 해소**되는 구조였다.
- 결정: 개발자가 Next 16으로 상향을 지시. 16.3.6 적용 후 `npm audit` 0건.
- **왜 15.5.25가 아니라 16이었는지: 기억나지 않음** (2026-11-03 확인). 보안 권고 해소가
  동기였던 것은 기록상 분명하나, 메이저 상향을 선택한 판단 근거는 남아 있지 않다.
- 부수 작업 2건:
  - `eslint-config-next` 16이 flat config 네이티브가 되면서 `FlatCompat` 브리지가 깨졌다
    (`TypeError: Converting circular structure to JSON`). flat config 배열을 직접
    스프레드하도록 고치고 `@eslint/eslintrc` 의존성을 제거했다.
  - Next 16이 마운트된 `tsconfig.json` 을 자동 수정했다 (`jsx`: preserve → react-jsx,
    `include` 에 `.next/dev/types` 추가). 정상 동작이고 `strict: true` 는 보존됐다.
- `CLAUDE.md` 2장과 `DEVELOPMENT_GUIDE` 의 버전 표기도 16으로 갱신했다.
- 배운 것: 번들된 전이 의존성은 상위 패키지를 올려야만 패치된다. 직접 의존성만 올려서는
  `npm audit` 이 안 닫힌다.

---

## 2026-09-23 — 의존성을 바꿨는데 컨테이너가 계속 옛 버전으로 뜸

- 증상: `package.json` 의 next를 16으로 올리고 `docker compose up -d --build frontend` 를
  했는데 컨테이너 로그에 계속 `Next.js 15.5.25` 가 찍혔다.
- 원인: 이미지는 새로 빌드됐지만 **기존 익명 볼륨 `/app/node_modules` 가 재사용**되어
  새 이미지의 `node_modules` 를 가렸다.
- 해결: `docker compose up -d --build --force-recreate --renew-anon-volumes frontend`
- 배운 것: 익명 볼륨은 컨테이너를 다시 만들어도 살아남는다. 호스트 파일이 컨테이너를 가리는
  문제(`- /app/.venv` 를 넣는 이유)와 **방향만 다를 뿐 같은 종류의 함정**이다.
  `backend/pyproject.toml` 을 고칠 때 `/app/.venv` 에서 똑같이 재발한다.
  증상은 "분명히 추가했는데 컨테이너가 모듈을 못 찾음".
- 소요: 15분

---

## 2026-11-03 — GitHub Actions 버전을 올렸더니 CI가 resolve 실패

- 증상: PR #2에서 backend 잡만 3초 만에 실패.
  `Unable to resolve action 'astral-sh/setup-uv@v10', unable to find version 'v10'`
- 원인: 액션 버전을 올릴 때 "최신 릴리스가 v10.2.0"인 것만 확인하고, GitHub 액션의 관례대로
  **떠 있는 메이저 태그 `v10`이 당연히 있을 것이라 가정**했다.
  `astral-sh/setup-uv` 는 `v7` 이후 떠 있는 메이저 태그를 발행하지 않는다
  (존재하는 메이저 태그: v4, v5, v6, v7 / 전체 버전 태그: v10.3.3, v10.2.0 …).
  `actions/checkout@v7` 과 `actions/setup-node@v5` 는 메이저 태그가 있어 resolve 됐고,
  그래서 frontend 잡만 초록불이었다.
- 해결: `astral-sh/setup-uv@v10.3.3` 으로 전체 버전 고정.
  `v7`로 내리는 선택지도 있었으나 3개 메이저 뒤처지고, 업스트림이 떠 있는 태그 발행을
  중단했으므로 최신을 쓰려면 전체 버전 고정이 유일한 방법이다.
- 배운 것: **"최신 릴리스 번호"와 "참조 가능한 태그"는 다르다.** 액션 버전을 바꿀 때는
  릴리스 목록이 아니라 태그 존재 여부를 직접 확인해야 한다.
  커밋 전 검증을 절차로 추가했다 — 워크플로의 모든 `uses:` 를 뽑아
  `git/ref/tags/{tag}` 가 200인지, 런타임이 node24인지 확인한다.
- 소요: 20분

---

## 2026-11-06 — 동속을 bool 로 뭉개지 않기로

- 상황: `resolve_order()` 의 반환 타입을 정해야 했다. "내가 더 빠른가"를 bool 로
  돌려주는 게 가장 단순하다.
- 문제: 동속(실수치가 같음)이 `False` 에 섞여 들어간다. 실제로는 50% 확률인데
  "내가 느리다"와 구분이 안 된다.
- 결정: `TurnOrder.FASTER / SLOWER / SPEED_TIE` 세 값으로 나눴다.
  Claude 가 제안했고 개발자가 추천대로 수용.
- 왜: 이 서비스는 수치가 아니라 **근거**를 파는 제품이다. "선봉으로 내면 먼저 움직인다"와
  "동속이라 반반이다"는 선출 판단이 달라진다. 타입으로 구분을 강제해두면 상위 계층이
  이 정보를 버릴 수 없다.
- 같은 맥락: `points_to_outspeed()` 도 동속을 추월로 치지 않는다. 상대 실수치를 초과해야 한다.
- 소요: 10분

## 2026-11-06 — "호출자"가 누구인지 혼동

- 상황: `resolve_order(my_priority=...)` 를 두고 "기술 우선도를 사용자가 직접 넣어야 하느냐"는
  질문이 나왔다.
- 원인: 설명에서 "호출자가 넘긴다"고만 썼다. 여기서 호출자는 서비스 이용자가 아니라
  `services` 계층의 코드인데 그게 드러나지 않았다.
- 실제 흐름: `moves.json`(미작성) → services 계층이 조회 → domain 함수에 숫자로 전달.
  화면에서는 기술 이름만 고르면 된다.
- 배운 것: 계층 설계를 설명할 때 "호출자"처럼 코드 관점의 단어를 쓰면
  제품 관점의 "사용자"와 섞인다. **어느 계층인지 이름을 박아서** 말해야 한다.
- 소요: 5분


---

## 2026-11-10 — 벽·필드 배율을 도메인에 박지 않기로

- 상황: 보정을 솔버에 배선하면서 벽(리플렉터·빛의장막·오로라베일)과 필드를
  어떻게 받을지 정해야 했다. 벽은 개발자가 **0.5 로 확인**해줬다.
- 선택지: (a) 도메인 상수로 박고 `screen: "reflect"` 처럼 종류로 받기,
  (b) 호출자가 배율을 넘기기.
- 결정: **(b).** 벽 수치는 확인됐지만 **필드는 챔피언스 자료가 전혀 없다.**
  둘을 다르게 다루면 "왜 벽은 enum 이고 필드는 float 이지?"가 남는다.
  ADR-007 의 "도메인은 결정된 배율만 받는다"와도 일관된다.
- 되돌릴 시점: 필드 배율까지 확인되면 둘 다 enum 으로 올린다. 그때는
  `items.json` 처럼 `source` 를 함께 기록한다.
- 소요: 15분

## 2026-11-10 — 검증이 지연돼 있어 "제약이 있을 때만" 걸리던 문제

- 증상: `solve()` 에 잘못된 도구 id(`mystery`)나 범위 밖 랭크(+7)를 넘겨도
  제약 리스트가 비어 있으면 예외가 나지 않았다.
- 원인: 도구 조회와 랭크 적용이 **후보 탐색 안에서** 일어났다. 계산을 안 하면
  검증도 안 된다. 테스트를 쓰다가 드러났다.
- 해결: 도구는 `solve()` 진입 시 전부 해석하고, 랭크 범위는
  `BuildTarget.__post_init__` 에서 막는다.
- 배운 것: **지연 검증은 "입력이 특정 조건일 때만 틀리는" 버그를 만든다.**
  사용자는 제약 없이 한 번 호출해보고 "통과했으니 맞는 입력"이라고 판단한다.
  입력 검증은 쓰이는 지점이 아니라 **받는 지점**에 둔다.
- 소요: 20분

## 2026-11-08 — 기술 수치는 본가와 같지 않았다 (ADR-009)

- 증상: Phase 2-2 시작 시 "종족값이 본가와 같았으니 기술도 같을 것"이라 가정하고
  PokéAPI에서 받으려 했다.
- 원인: 틀린 가정이었다. 챔피언스는 밸런스 패치로 위력·명중을 조정하고 PP를
  {1,8,12,16,20}으로 정규화했다. 부리캐논 100→120, 강철날개 PP 25→20,
  드래곤클로는 '베기'로 재분류. 기술 수도 954(본가) vs 515(챔피언스)로 다르다.
- 해결: op.gg 챔피언스 기술 표를 주 출처로 쓰고, champions-helper·smartnuo·
  사용률 API 세 곳과 교차검증하는 생성기를 만들었다.
- 배운 것: **"한 종류가 같으면 다른 종류도 같다"는 추론이 두 번 연속 틀렸다.**
  Phase 2-1에서는 종족값이라고 부르는 값이 실수치였고, 여기서는 기술이 본가와
  달랐다. 출처가 주는 숫자는 **항목마다 따로 확인해야 한다.**
  가장 위험한 후보가 가장 그럴듯해 보였다는 점도 기억할 만하다 —
  `pokemon-champions-data` 라는 저장소 이름에 CC-BY 라이선스까지 붙어 있었지만
  내용은 순수 Showdown 덤프였다.
- 소요: 90분 (조사 70분 / 생성기 20분)

## 2026-11-08 — 교차검증이 실제로 3건을 잡았다

- 증상: 생성기 첫 실행에서 검증 3건 실패. 전부 사람이 판단해야 하는 건이었다.
- 원인과 처리:
  - `comeuppance` — op.gg는 변동 위력을 '-', champions-helper는 `1`로 표기.
    **값 충돌이 아니라 표기법 차이.** 고정 위력이 없는 기술은 비교하지 않고
    "둘 다 고정 위력이 없다"만 확인하도록 고쳤다.
  - `rock-blast` — op.gg '록블라스트' vs smartnuo '록블레스트'.
    champions-helper가 op.gg와 같아 2:1로 판정.
  - `howl` — op.gg '멀리짖기' vs smartnuo '멀리짖음'. champions-helper에 없어
    2:1 판정이 불가했다. 제4 출처(gamewith.ai 챔피언스 한글 기술 DB)로 '멀리짖기' 확인.
- 배운 것: 화이트리스트에 **근거를 함께 적지 않으면 그건 그냥 눈 감는 것**이다.
  `EXPECTED_KO_DIFFS_SMARTNUO`에 어느 제3 출처로 확인했는지를 주석으로 남겼다.
  그리고 **출처가 2개면 충돌 시 판정이 불가능하다** — 3개가 최소선이다.
- 소요: 35분

## 2026-11-08 — Phase 5 learnset 출처 후보 (미결)

- 상황: "어떤 포켓몬이 어떤 기술을 배우는지"는 파티 빌더(Phase 5)에 필요한데
  op.gg 기술 표에도, championsbattledata API에도 없다.
- 찾은 것:
  - `gamewith.ai/pokemon-champions/ko/moves/{id}` — 기술별 "배우는 포켓몬 목록".
    챔피언스 전용 한글 DB. 단 일부 페이지에 "포켓몬 SV 정보 기준이므로 정확하지
    않을 수 있습니다" 경고가 붙어 있어 그대로 믿으면 안 된다.
  - `smartnuo.com/api/pokemon_move` — 200을 돌려주지만 파라미터를 몰라 빈 배열만 온다.
    Nuxt 번들을 읽으면 알 수 있을 것이다.
  - `smartnuo.com/api/pokemon` — kr/en/jp + smogon_name 1,312건. **Phase 2-3
    한영 매핑의 유력 후보.** `/api/ability`(55KB)도 열려 있다.
- 다음에 할 일: Phase 2-3에서 `/api/pokemon`을 ADR-005의 종족값 교차검증과 함께 평가한다.
  learnset은 Phase 5에서 두 출처를 대조해 결정한다. **단일 출처로 가지 않는다.**
- 소요: 15분 (탐색만)

## 2026-11-08 — 전송 오류 한 번에 5분짜리 검증이 전부 날아간 문제

- 증상: 사용률 API 263종 전수 조인 검증이 중간에 `IncompleteRead` 로 죽었다.
  앞에서 통과한 검증 결과까지 같이 버려졌다.
- 원인: `HTTPError` 만 잡고 있었다. 응답이 절반에서 끊기는 건 HTTP 상태 오류가
  아니라 전송 오류라서 그대로 터졌다.
- 해결: 전송 오류만 3회 재시도(backoff 3초)하고 404 와 HTTP 상태 오류는 그대로 올린다.
- 판단이 갈린 지점: 재시도를 다 쓴 뒤에 **건너뛸지 실패시킬지.**
  건너뛰기로 했다면 생성은 성공하고 `moves.json` 은 멱없이 쓰였을 것이다.
  그런데 이 검증의 목적이 "전수 조인이 된다"는 보증이라서, 한 종을 못 읽으면
  그 보증을 할 수 없다. → `errors` 에 넣어 생성을 중단한다.
- 배운 것: **데이터가 틀린 것과 네트워크가 끊긴 것은 다르게 다뤄야 한다.**
  전자는 재시도해도 같고, 후자는 재시도가 맞다. 다만 재시도가 소진된 뒤의 처리는
  다시 "이 검증이 무엇을 보증하는가"로 돌아가서 정해야 한다 —
  관례적으로 건너뛰면 보증이 조용히 약해진다.
- 소요: 15분

## 2026-11-08 — `airborne` 이라는 이름이 데이터보다 많은 걸 말하고 있었다

- 상황: op.gg 기술 표의 플래그 '압박'·'상승'은 본가에 대응하는 이름이 없어
  내가 영문 키를 정해야 했다. 멤버 구성을 근거로 '압박'→`minimize_boosted`,
  '상승'→`airborne` 이라고 붙였다.
- 사용자 정정: '압박'은 맞았지만 **'상승'은 하나의 성질이 아니었다.**
  - 공중날기·튕겨오르기 = 1턴차 상승으로 회피(상대가 느리면), 2턴차 데미지. 예외 기술 있음
  - 전자부유 = 5턴간 '부유' 특성 → 땅 타입 무효
  - 무릎차기·플라잉프레스 = **효과 없음.** 설명문에 '공중'이라는 단어만 있다
- 처리: `rises` 로 바꿨다. "op.gg 가 '상승'으로 묶었다"는 사실만 말하고 기계적 성질은
  주장하지 않는 이름이다. `_meta.flags_named_by_us` 에 `usable_as_a_group: false` 를
  넣어 엔진이 이 플래그로 판단하지 않음을 데이터 자체에 박았다.
- 배운 것: 내 추론의 **방향**은 그럴듯했다. 5개 전부 '사용자가 뜬다'로 읽혔고
  멤버 구성도 일관돼 보였다. 틀린 건 **그 공통점이 기계적으로 의미가 있다고
  가정한 부분**이다. 멤버 5개 중 2개는 애초에 효과가 없었다.
  → 이름은 아는 것만 말해야 한다. `airborne` 은 "뜬다 → 그래서 무언가 일어난다"를
    함께 주장했는데, 내가 안 건 앞쪽 절반뿐이었다.
  → CLAUDE.md 1-3("추정과 사실을 구분한다")이 응답 스키마뿐 아니라
    **식별자 이름에도 적용된다.** 이름은 가장 조용한 형태의 주장이다.
- 소요: 20분

## 2026-11-09 — 출처의 `form_kind` 를 판정 근거로 쓴 게 두 번째 실수였다

- 증상: 한영 매핑 생성기가 유일성 검증에서 멈췄다.
  `'파밀리쥐'` → maushold / mausholdfour, `'시비꼬'` → squawkabilly / squawkabillyyellow.
- 원인: 자동 확정 조건에 "`form_kind == Base` 면 폼이 아니다"를 넣었는데,
  championsbattledata 가 `mausholdfour` 와 `squawkabillyyellow` 를 **양쪽 다 Base** 로
  표기한다. 그래서 종 명칭이 두 id 에 붙었다.
- 해결: 충돌 시 **에러가 아니라 겹친 항목을 전부 검수로 내린다.** 멈추면 검수할
  기회가 없고, 한쪽만 남기면 어느 쪽이 진짜인지 모른다.
- 배운 것: 어제 `vivillonfancy` 의 form_kind 가 id 와 어긋난 걸 보고도 "그 항목만
  이상하다"로 처리했다. **같은 필드가 두 번 어긋났으면 그건 항목 문제가 아니라
  필드 문제다.** 출처의 분류 필드는 참고 정보이지 판정 근거가 아니다.
  어제 발견했을 때 한 걸음 더 갔으면 오늘 멈추지 않았다.
- 소요: 25분

## 2026-11-09 — 종족값만으로는 폼 가족 안에서 구분이 안 된다

- 상황: ADR-005 의 핵심 방어선이 "종족값 교차검증"이었다.
- 발견: **로토무 5폼과 켄타로스 3품종은 종족값이 전부 같고 타입만 다르다.**
  종족값만 보면 히트로토무에 워시로토무 이름을 붙여도 검증을 통과한다.
- 해결: 자동 확정 조건에 타입 일치를 추가했다. 263종 전부 두 조건을 통과했다.
- 배운 것: 교차검증 항목을 고를 때 **"그 값이 구분하려는 대상 안에서 변하는가"**
  를 봐야 한다. 종족값은 종끼리는 잘 구분하지만 폼끼리는 구분하지 못한다.
  방어선을 세울 때 "무엇을 막는가"까지 적어야 이런 구멍이 안 생긴다.
- 소요: 15분

## 2026-11-09 — 검수를 '승인 절차'로 만들지 않기

- 상황: 폼 40건의 한글 표기를 사람이 확정해야 했다. 40건을 대화로 주고받는 건
  비효율적이라 초안을 채운 파일을 만들어 에디터에서 고치는 방식으로 갔다.
- 문제: 초안을 생성기가 그냥 받아들이면 **"사람 검수"가 기계 출력에 도장을 찍는
  절차**가 된다. ADR-005 의 방어선 6 이 무력화된다.
- 해결: 항목마다 `confirmed` 플래그를 두고 **사람이 뒤집은 것만** 반영한다.
  근거가 약한 17건은 `ko` 를 **비워둔 채로** 올렸다 — 그럴듯한 값을 채워 넣으면
  확인이 아니라 승인이 된다.
- 배운 것: 사람을 루프에 넣는 것과 **사람이 실제로 판단하게 하는 것**은 다르다.
  기본값이 채워져 있으면 사람은 읽지 않고 넘긴다. 비워두는 게 불친절해 보이지만
  그게 검수를 검수로 유지한다.
- 소요: 20분

## 2026-11-09 — 종족값이 없는 대상의 교차검증 재료 찾기

- 상황: ADR-005 의 방어선 2 가 "종족값 교차검증"인데, 성격·특성·도구에는 종족값이 없다.
  어제 ADR 에 "세 가지 모두 방어선 2 가 빠진 상태로 가야 한다"고 적었다.
- 그게 틀렸다. **각 대상이 가진 다른 사실을 쓰면 된다.**
  - 성격 → **상승/하락 스탯 쌍.** 20가지 쌍이 전부 다르고 무보정 5개는 둘 다 없다.
    쌍이 맞으면 그 성격이 맞다. 우리 `natures.json` 에 이미 있어 대조만 하면 된다.
  - 특성 → **그 특성을 가진 포켓몬 목록.** `name_map.json` 이 포켓몬별 PokéAPI
    슬러그를 들고 있어서 ID 로 교집합을 볼 수 있다. 하나도 겹치지 않으면 거부한다.
  - 도구 → 여전히 재료가 없다. 별도 작업으로 분리했다.
- 결과: 성격 25/25, 특성 202/202 확정. 보류 0.
- 배운 것: "교차검증할 숫자가 없다"에서 멈추지 말고 **"이 대상이 틀렸을 때
  같이 틀어지는 다른 값이 무엇인가"** 를 묻는다. 종족값이 특별했던 게 아니라,
  '이름과 함께 움직이는 독립된 사실'이었다는 점이 특별했다.
- 소요: 40분

## 2026-11-09 — 내 기대값이 틀렸는데 골든 케이스처럼 보였던 경우

- 증상: 테스트 4건 실패. `jolly` 의 한글명이 '천진난만'이 아니라 '명랑'이라고 나왔다.
  `roughskin` 도 '거친피부'가 아니라 '까칠한피부'였다.
- 확인: **데이터가 맞았다.** 천진난만은 Naive(스피드↑ 특방↓)이고 명랑이 Jolly
  (스피드↑ 특공↓)다. CLAUDE.md 4절의 예시 `A10C24S32 천진난만` 도 Naive 다.
- 처리: 기대값을 고쳤다. CLAUDE.md 가 금지하는 "테스트 기대값을 바꿔 통과시키기"와
  구분이 필요한 지점이라 근거를 남긴다 — 금지되는 건 **엔진이 틀렸는데 기대값을
  맞추는 것**이고, 여기서는 내가 외운 한글명이 틀렸다. 보정 쌍 교차검증이
  "이 한글명은 스피드↑ 특공↓ 성격의 것"임을 이미 보장한 상태였다.
- 배운 것: 검증 장치가 통과한 데이터와 내 기억이 충돌하면 **기억을 먼저 의심한다.**
  장치는 근거를 제시할 수 있고 기억은 못 한다. naive/timid/adamant/modest 를 같이
  뽑아 보정 쌍과 대조하니 한 번에 판정됐다.
- 소요: 10분

## 2026-11-13 — pytest-asyncio 와 캐시된 엔진이 안 맞았다

- 증상: DB 통합 테스트에서 `RuntimeError: Event loop is closed`.
  일부는 FAILED, 일부는 ERROR 로 섞여 나와 원인이 안 보였다.
- 원인: 운영 코드의 엔진이 `@lru_cache` 로 프로세스당 하나인데, pytest-asyncio 는
  **테스트마다 이벤트 루프를 새로 만든다.** asyncpg 커넥션이 이전 루프에 묶인 채
  풀에 남아 있다가 다음 테스트에서 터졌다.
- 해결: 테스트 픽스처가 엔진을 직접 만들고 `NullPool` 을 쓴다. 테스트가 끝나면 dispose 한다.
- 다른 선택지: 세션 스코프 이벤트 루프로 통일하는 방법도 있었다. 안 쓴 이유는
  테스트 간 격리가 약해지기 때문이다 — 한 테스트가 루프에 남긴 상태가 다음으로 샌다.
- 배운 것: **풀링은 "같은 루프에서 재사용한다"는 가정 위에 서 있다.** 운영에서는 맞는
  가정이고 테스트에서는 아니다. 운영 코드를 테스트에 맞춰 고치는 대신
  테스트가 자기 엔진을 갖게 하는 쪽이 옳았다.
- 소요: 20분

## 2026-11-13 — 생성되는 코드가 lint 에 걸릴 때는 템플릿을 고친다

- 증상: `alembic revision --autogenerate` 가 만든 마이그레이션이 ruff 에 7건 걸렸다
  (`Union[str, None]`, import 순서).
- 처음 생각: `--fix` 로 고치고 넘어간다.
- 문제: **마이그레이션을 만들 때마다 반복된다.** 앞으로 수십 개가 생길 파일이다.
- 해결: `alembic/script.py.mako` 템플릿을 고쳤다. 앞으로 생성되는 것은 처음부터 통과한다.
- 배운 것: 반복될 수작업이 보이면 **결과물이 아니라 생성기를 고친다.**
  시드 생성기에서 같은 판단을 여러 번 했는데, 코드 생성 템플릿에도 똑같이 적용된다.
- 소요: 10분

## 2026-11-13 — 이미 받고 있던 응답에 learnset 이 들어 있었다

- 상황: Phase 2-2 에서 "learnset 은 op.gg 기술 표에도 championsbattledata API 에도
  없다"고 판단하고 Phase 5 과제로 미뤘다. gamewith.ai·smartnuo 를 후보로 적어뒀다.
- 발견: ETL 설계하며 인덱스 엔트리를 다시 보니 **`learnableMoveNames`** 가 있었다.
  한카리아스 59개. 우리가 매번 받는 그 응답이다.
- 원인: 그때 `summary` 안쪽(종족값·타입)만 파고들고 **엔트리 최상위 키를 끝까지
  나열해보지 않았다.** 찾던 게 거기 없다는 걸 확인하는 대신, 보던 자리에 없으니
  없다고 결론 냈다.
- 영향: Phase 2-4 에서 `pokemon_learnset` 테이블을 "데이터 없음"을 이유로 뺐는데
  그 근거가 틀렸다. 다시 넣어야 한다.
- 배운 것: **새 출처를 찾기 전에 이미 받는 응답의 키를 전부 출력해본다.**
  비용이 한 줄인데 두 번(기술 수치, learnset) 놓쳤다.
- 소요: 탐색 중 발견, 추가 비용 없음

## 2026-11-13 — 출처가 안 주는 값을 우리가 만들 수 있는 경우

- 상황: 사용자가 "순위 상승/하강 정보가 메타 파악에 도움이 되면 좋겠다"고 했다.
  championsbattledata 는 순위 변동을 주지 않는다. op.gg 티어 페이지에는 변동 표시가
  있지만, 그걸 쓰면 **CLAUDE.md 8-2 에 내가 적은 "런타임 호출 없음, 스케줄러에
  올리지 않는다"와 충돌**한다.
- 해결: 외부에서 가져오지 않고 **우리 스냅샷에서 파생**했다. 보존 기간이 14일이라
  직전 스냅샷의 rank 와 비교하면 변동이 나온다. `data.rank_change` 에 담는다.
- 배운 것: "출처가 X 를 안 준다"와 "X 를 만들 수 없다"는 다르다.
  우리가 이미 보관하는 것에서 유도되는지 먼저 본다. 새 출처를 붙이면 약관·요청량·
  장애 지점이 전부 늘어나는데, 이 경우엔 하나도 늘지 않았다.
- 남은 것: op.gg 사용률 %는 별개 문제다. 초기 HTML 에 262마리 중 18행만 있고
  사용률 컬럼이 없어, 쓰려면 정책 충돌을 먼저 정리해야 한다.
- 소요: 30분

## 2026-11-13 — 출처가 밝히는 개수를 '완전함'으로 읽었다

- 증상: learnset 을 적재하려고 16,037쌍을 기술표와 대조했더니 미해석 1건
  (`thundershock`, 로토무 5폼이 배운다).
- 확인: op.gg **목록 페이지**에는 없지만 **상세 페이지**는 존재하고 챔피언스 수치를
  갖고 있다 (특수/위력 40/명중 100/PP 20). PP 20 은 챔피언스 정규화 값
  {1,8,12,16,20} 중 하나라 형태도 맞다. championsbattledata 도 로토무가 배운다고 한다.
- 원인: ADR-009 작업 때 목록이 "515 기술"이라 밝히고 파싱 결과도 515개라
  **일치하니 완전하다**고 판단했다. 그런데 그건 "목록에 515개가 있다"를 확인한 것이지
  "챔피언스 기술이 515개다"를 확인한 게 아니다. 같은 출처 안에서 자기 자신과
  대조한 셈이라 검증이 아니었다.
- 해결: 생성기에 보완 단계를 넣었다 — learnset 이 참조하는데 목록에 없는 기술은
  상세 페이지에서 가져오고, 거기에도 없으면 **실패시킨다**. 516개가 됐다.
- 배운 것: **출처가 스스로 밝히는 개수는 교차검증이 아니다.** 독립된 다른 데이터와
  맞춰봐야 한다. 여기서는 learnset 이 그 역할을 했고, 그건 원래 다른 목적으로
  가져온 데이터였다. 가진 데이터끼리 교차시킬 자리를 더 찾아볼 만하다.
- 소요: 40분

## 2026-11-13 — "Current" 는 시즌이 아니라 별칭이었다

- 상황: 수집 CLI 가 레귤레이션을 만들어야 하는데 `regulations` 테이블이 비어 있었다.
  코드로 뭘 쓸지 정해야 했고, API 응답의 `season` 이 "Current" 였다.
- 발견: 인덱스를 보니 `seasons: ["Current", "M7", "M6", ...]` 이고 실제 데이터 폴더와
  CSV 경로는 **"M7"** 이다. 즉 "Current" 는 최신 시즌을 가리키는 포인터다.
- 왜 중요한가: "Current" 를 레귤레이션 코드로 쓰면 시즌이 M8 로 넘어갈 때
  **같은 레귤레이션 행에 다른 시즌 데이터가 쌓인다.** 스냅샷의 스코프가 조용히
  무너지고, "이 사용률은 어느 시즌 것인가"에 답할 수 없게 된다.
- 해결: `resolve_season()` 이 별칭을 구체 시즌으로 바꾼다. 못 바꾸면 **실패시킨다**.
  랭킹 응답이 `seasons` 목록을 이미 포함하고 있어 추가 요청은 없었다.
- 배운 것: **출처가 주는 식별자가 고정값인지 포인터인지 확인한다.**
  "Current", "latest", "default" 같은 이름은 거의 항상 포인터다. 그대로 키로 쓰면
  시간이 지나서야 틀린 게 드러난다 — 그때는 이미 데이터가 섞여 있다.
- 소요: 20분

## 2026-11-14 — 이미지가 Phase 0 에서 멈춰 있었다

- 증상: DockerOperator 설계 중 `docker run pokehelper-backend ls /app` 을 해보니
  `scripts/`, `data/`, `alembic/` 이 **없었다**. CLI 를 실행할 수 없는 이미지였다.
- 원인: `.dockerignore` 문제가 아니라 **이미지가 Phase 0 때 빌드된 채로 멈춰 있었다.**
  compose 가 `./backend:/app` 을 바인드 마운트해서 개발 중에는 이미지 내용이 보이지
  않았다. 그 뒤 추가한 디렉터리가 전부 빠져 있었는데 아무도 몰랐다.
- 해결: `docker compose build backend`.
- 배운 것: **바인드 마운트는 이미지의 노후화를 가린다.** 개발 중에는 호스트 파일을
  보니 멀쩡하고, 이미지에서 직접 실행하는 순간(CI, 배포, 이번처럼 DockerOperator)
  드러난다. DockerOperator 가 아니었으면 Phase 6 배포에서야 알았을 것이다.
  → 이미지로 실행하는 경로를 하나라도 두면 이런 간극이 일찍 드러난다.
- 소요: 15분

## 2026-11-14 — Airflow 와 우리 백엔드는 한 환경에서 못 산다

- 상황: Airflow 를 처음 붙이면서 "DAG 에서 우리 코드를 import 하면 되지 않나"로 시작했다.
- 확인: Airflow 3.4.0 constraints 파일을 직접 받아보니 `SQLAlchemy==2.0.55` 였다.
  우리는 2.1.8 이다. 한 환경에 넣으면 둘 중 하나가 자기가 요구하지 않은 버전으로 돈다.
- 해결: DockerOperator 로 `pokehelper-backend` 이미지를 별도 컨테이너로 띄운다.
  두 환경이 서로를 모른다 (ADR-012).
- 처음에 provider 버전을 4.5.1 로 **추측해서 적었다가** constraints 를 확인하니
  4.6.1 이었다. 버전은 추측하지 말고 constraints 에서 읽는다.
- 배운 것: 프레임워크를 붙일 때 **그 프레임워크가 무엇을 고정하는지 먼저 본다.**
  "설치가 되는가"가 아니라 "내 의존성과 양립하는가"가 질문이다. constraints 파일 한 번
  받아보는 데 10초 걸렸고, 그걸로 설계 방향이 정해졌다.
- 소요: 25분

## 2026-11-17 — 요청 간격 1초가 실제로는 지켜지지 않았다

- 증상: 수집 어댑터에 요청 예절 단위 테스트를 처음 붙였더니, 목록 요청과 첫 상세
  요청 사이의 대기가 **0 건**이었다. 요청 3회에 대기 1회만 발생했다.
- 기대 동작: 요청 3회(목록 1 + 상세 2)면 대기는 2회여야 한다 —
  CLAUDE.md 8-6 "요청 간 최소 1초 지연", 가이드 4-4 약관 준수 체크리스트.
- 재현 절차:
  1. `git apply <저장소 밖에 둔 revert-gate.patch>` (게이트 연결만 되돌린다)
  2. `docker compose exec backend uv run pytest tests/unit/ingestion/test_championsbattledata.py`
  3. `TestRequestPoliteness` 2건 실패: `assert [] == [1.0 ± 1.0e-06, 1.0 ± 1.0e-06]`
  4. `git apply -R <같은 패치>` 로 복구
- 원인: 간격을 **포켓몬 루프 안에서** 셌다.

  ```python
  for index, row in enumerate(rankings["rankings"]):
      if index > 0:                                  # ← 루프 기준
          await asyncio.sleep(REQUEST_DELAY_SECONDS)
      battle = await self._get(...)
  ```

  `/rankings` 는 루프 **앞**에서 호출된다. `index == 0` 일 때 두 요청이 연달아 나간다.
  동시성 1 도 같은 종류의 문제였다 — 순차 `for` 루프라서 *우연히* 만족하고 있었고,
  누가 `asyncio.gather` 로 바꾸면 아무것도 막지 않는다.
- 해결: `ingestion/gate.py` 의 `RequestGate` 를 만들어 `_get` 안에서만 요청을 보낸다.
  모든 요청이 한 문을 지나가므로 호출 구조가 바뀌어도 간격이 유지된다. 동시성은
  `asyncio.Lock` 으로 직렬화한다. 시계와 `sleep` 은 주입받는다.
- 배운 것: **체크리스트에 체크가 되어 있어도 지켜지는 게 아니다.** 가이드 4-4 의
  "요청 간격 1초 이상" 항목은 체크돼 있었고, 상수도 `REQUEST_DELAY_SECONDS = 1.0`
  으로 선언돼 있었다. 빠진 것은 **그 상수가 호출 경로를 실제로 통과하는지 확인하는
  테스트**였다. 정책을 상수로 선언하는 것과 구조로 강제하는 것은 다르다.
  → 외부에 영향을 주는 정책은 "선언했다"가 아니라 "테스트가 있다"로 끝내야 한다.
- 덧붙여: 수정 전 코드로 같은 테스트 20건을 돌리면 **31.78초**, 수정 후는 **0.62초**다.
  전자는 진짜 `asyncio.sleep` 을 기다린다. 대기를 주입 가능하게 만들지 않으면
  예절 테스트는 느려서 결국 아무도 돌리지 않게 된다.
- 소요: 50분

## 2026-11-17 — 테스트가 틀렸는데 구현이 맞았던 경우

- 증상: 위 게이트를 구현한 뒤 전체 테스트에서 1건 실패.
  `test_gap_is_measured_from_the_end_of_the_previous_request` 가
  `assert [1.0] == []` 로 깨졌다.
- 기대 동작: 어느 쪽이 기대 동작인지가 **이 건의 쟁점 자체였다.** 처음 적은 기대값은
  "응답이 2초 걸렸으면 다음 요청은 즉시 나간다"였다.
- 재현 절차:
  1. `git log -p -1 --  backend/tests/unit/ingestion/test_request_gate.py` 로 수정 전 기대값 확인
  2. 그 기대값(`assert sleep.waits == []`)으로 되돌린다
  3. `docker compose exec backend uv run pytest tests/unit/ingestion/test_request_gate.py`
- 원인: **내가 테스트 독스트링에 근거를 거꾸로 썼다.** "응답이 2초 걸렸으면 이미
  2초의 공백이 있었다"고 적었는데, 그 2초 동안 출처는 **우리 요청을 처리하고 있었다.**
  공백이 아니라 그 반대다. 두 해석이 갈린다:

  | 기준 | 의미 | 응답이 2초 걸린 뒤 |
  |---|---|---|
  | 끝난 시각 | 요청 사이에 1초의 정적 | 1초 더 기다린다 |
  | 시작한 시각 | 초당 1요청 (rate limit) | 즉시 다음 요청 |

- 해결: 구현(끝난 시각 기준)을 두고 **기대값을 고쳤다.** 근거는 CLAUDE.md 8-6 의
  문구 "요청 **간** 최소 1초 **지연**" 과 "고정 rate limit 이 없다고 몰아치지 않는다" —
  둘 다 보수적인 쪽을 가리킨다. 테스트 이름도
  `test_a_slow_response_does_not_excuse_the_gap` 으로 바꿨다.
- 배운 것: 기대값을 고치는 건 원칙적으로 금지다(CLAUDE.md 하지 말 것). 그 금지는
  **"구현을 통과시키려고" 고치는 경우**를 막는 것이고, 여기는 방향이 반대였다 —
  오라클이 틀렸다는 근거를 구현과 **독립적으로**(규칙 문서의 문구) 확인한 뒤 고쳤다.
  `docs/TEST_STRATEGY.md` 4-4 에 적어둔 판정 기준을 실제로 적용한 두 번째 사례다.
  → 판정 기준을 미리 문서에 적어두면, 이런 순간에 "고쳐도 되나"를 즉흥 판단하지 않는다.
- 소요: 15분

## 2026-11-17 — 저장소 루트에 만든 `scripts/` 가 기존 패키지를 가렸다

- 증상: 리허설 테스트를 처음 돌리자 **관계없는 통합테스트가 수집 단계에서 깨졌다.**
  `ModuleNotFoundError: No module named 'scripts.run_ingest'`, 604 deselected / 1 error
- 기대 동작: 백업 스크립트를 추가해도 기존 테스트 615개는 그대로 통과해야 한다
- 재현 절차:
  1. `mkdir scripts && touch scripts/backup.sh`
  2. `docker-compose.yml` 의 backend volumes 에 `- ./scripts:/app/scripts:ro` 추가
  3. `docker compose up -d backend`
  4. `docker compose exec backend uv run pytest tests/integration/test_scheduler_commands.py`
- 원인: 프롬프트가 `scripts/backup.sh` 라고 지정해서 저장소 루트에 `scripts/` 를 만들고
  컨테이너의 `/app/scripts` 에 마운트했다. 그런데 **`backend/scripts/` 가 이미
  파이썬 패키지로 존재했다** (`run_ingest.py`, `cleanup_snapshots.py`, 시드 생성기 8개).
  `./backend:/app` 위에 `./scripts:/app/scripts` 를 얹으면 그 패키지가 **통째로 가려진다.**
  파일이 지워진 게 아니라 안 보이게 된 것이라, 호스트에서 `ls` 로는 멀쩡해 보인다.
- 해결: `backend/scripts/` 로 옮기고 마운트를 제거했다. 운영 CLI 의 집은 이미 거기였다.
- 배운 것: 프롬프트에 적힌 경로보다 **저장소에 이미 있는 컨벤션이 우선**이다
  (CLAUDE.md 9장 "기존 코드의 컨벤션을 따른다"). 그리고 바인드 마운트는 덮어쓰기가
  아니라 **가리기**다 — 컨테이너 안에서 `ls` 를 해봐야 알 수 있다.
  ADR-012 의 "바인드 마운트는 컨테이너가 빌드 시 만든 파일을 덮어쓴다"와 같은 함정의
  다른 얼굴이다.
- 소요: 약 20분

## 2026-11-17 — 백업은 되는데 복구가 안 되는 조합 (리허설이 첫 실행에서 잡았다)

- 증상: 복구 리허설 테스트가 복구 단계에서 실패.
  `pg_restore: error: could not execute query: ERROR: unrecognized configuration
  parameter "transaction_timeout"` / `Command was: SET transaction_timeout = 0;`
  **백업(`pg_dump`)은 성공했고 파일도 정상 크기였다.**
- 기대 동작: `backup.sh` 가 만든 덤프를 `restore.sh` 로 되돌릴 수 있어야 한다
- 재현 절차:
  1. `backend/Dockerfile` 의 dev 타깃에서 `postgresql-client-16` 을
     `postgresql-client` (Debian 기본값)로 바꾼다
  2. `docker compose build backend && docker compose up -d backend`
  3. `docker compose --profile rehearsal up -d db-rehearsal`
  4. `docker compose exec backend uv run pytest -m rehearsal`
- 원인: 클라이언트와 서버의 메이저 버전이 어긋났다.

  | | 버전 | 결과 |
  |---|---|---|
  | 서버 (`postgres:16-alpine`) | 16 | |
  | `python:3.12-slim` 의 Debian 기본 클라이언트 | **17** | |
  | pg_dump 17 → 서버 16 | | **성공** |
  | pg_restore 17 → 서버 16 | | **실패** |

  `transaction_timeout` 은 PG17 에 추가된 설정이고, pg_restore 17 이 복구 서두에
  무조건 `SET` 한다. 서버 16 은 그 이름을 모른다.
  내가 Dockerfile 주석에 "상위 클라이언트는 하위 서버에 괜찮다"고 적어둔 게 **덤프에는
  맞고 복구에는 틀렸다.** 애초에 베이스 이미지를 bookworm 으로 가정했던 것도 틀렸다 —
  `python:3.12-slim` 은 이미 trixie(Debian 13)다. 첫 빌드가 `libldap-2.5-0` 의존성
  오류로 깨진 게 그 신호였는데, 그때는 저장소 코드네임만 고치고 넘어갔다.
- 해결: PGDG 저장소에서 **서버와 같은 메이저(16)** 를 받는다. 코드네임은
  `/etc/os-release` 에서 읽어 하드코딩하지 않는다 — 베이스가 bookworm→trixie 로
  바뀐 적이 있고 그때 조용히 깨졌다.
- 배운 것: **이게 복구 리허설을 만든 이유 그 자체다.** 백업 스크립트만 만들고
  "pg_dump 가 0 으로 끝났다"로 끝냈다면, 멀쩡해 보이는 덤프가 쌓이는 상태로
  운영에 들어갔을 것이다. 정작 복구가 필요한 날에 알게 된다.
  "백업했다"의 종료 조건은 **덤프 생성이 아니라 복구 성공**이다.
- 소요: 약 45분 (이미지 재빌드 3회 포함)

## 2026-11-17 — 보존 기간 0일이 방금 만든 백업을 지웠다

- 증상: `BACKUP_RETENTION_DAYS=0` 으로 돌리면 `backup.sh` 가 성공(exit 0)하는데
  **디렉토리에 아무 백업도 남지 않는다.** 로그에 `보존 기간 0일 — 2건 삭제`
- 기대 동작: 보존 기간이 얼마든 **이번 실행이 만든 백업은 남아야 한다**
- 재현 절차:
  1. `backend/scripts/backup.sh` 의 `find` 에서 `! -name "$keep"` 를 지운다
  2. `docker compose --profile rehearsal up -d db-rehearsal`
  3. `docker compose exec backend uv run pytest -m rehearsal -k retention_zero`
- 원인: GNU `find` 의 `-mmin` 은 경과 시간을 **올림**한다. 3초 전에 만든 파일도
  "1분"으로 계산되므로 `-mmin +0` 에 걸린다. `-mtime` 대신 `-mmin` 을 고른 건
  경계를 테스트로 집기 위해서였는데, 그 선택이 0 의 경계에서 역효과를 냈다.
- 해결: 정리 대상에서 **이번 실행의 산출물을 이름으로 제외**했다
  (`! -name "$(basename "$final")"`). 설정값이 무엇이든 자기가 만든 백업을 지우는
  백업 스크립트는 성립하지 않는다.
- 배운 것: 경계값 테스트를 **설정의 양 끝까지** 넣어야 보인다. 7일 경계만 봤다면
  통과했다. 0 은 "쓰지 않을 값"처럼 보여서 빼기 쉬운데, 그런 값이 사고를 낸다.
  그리고 이 버그는 **exit 0 으로 성공을 보고했다** — 조용한 실패였다.
- 소요: 약 20분

## 2026-11-17 — CHECK 제약 정의가 복구 후 다르게 렌더링됐다 (기대값 변경 3번째 사례)

- 증상: 복구 전/후 제약 정의 문자열 비교가 `ck_moves_category` 하나에서 실패
  ```
  복구 전: ANY ((ARRAY['physical'::character varying, ...])::text[])
  복구 후: ANY (ARRAY[('physical'::character varying)::text, ...])
  ```
- 기대 동작: 복구 후에도 같은 제약이 같은 검사를 수행해야 한다
- 재현 절차:
  1. `backend/tests/rehearsal/test_restore_rehearsal.py` 에서
     `test_constraint_checks_survive_rerendering` 의 `_same_check(...)` 호출을 벗긴다
     (양쪽을 원문 그대로 비교하게 만든다)
  2. `docker compose exec backend uv run pytest -m rehearsal -k constraint`
- 원인: `pg_get_constraintdef` 은 저장된 문자열이 아니라 **파스 트리를 다시 렌더링한
  결과**다. pg_dump 가 식을 직렬화하고 복구가 다시 파싱하면, 캐스트를 배열 전체에
  걸었는지 원소마다 걸었는지가 달라진다. **의미는 동일하다.**
- 해결: 이 층의 오라클을 바로잡았다. 문자열 동일성은 애초에 우리가 지키려는 불변식이
  아니었다. 셋으로 쪼갰다.
  - 제약 **이름** 집합은 원문 그대로 비교 (하나라도 사라지거나 생기면 실패)
  - 제약 **정의**는 캐스트·괄호·공백을 걷어낸 형태로 비교 (값 목록이 바뀌면 여전히 실패)
  - 제약이 **집행되는지**는 위반을 실제로 INSERT 해서 거부되는지로 별도 단정
- 배운 것: 기대값을 고치는 건 원칙적으로 금지지만, 여기도 방향이 반대였다 —
  **오라클이 틀렸다는 근거가 구현과 독립적**이었다(두 식이 의미상 동등하다는 사실,
  그리고 집행 테스트가 따로 통과한다는 사실). `docs/TEST_STRATEGY.md` 4-4 의 판정
  기준을 적용한 세 번째 사례다.
  → 더 중요한 교훈: **"정의가 있다"와 "집행된다"는 다른 단정이다.** 카탈로그에
  정의가 남아 있어도 인덱스가 INVALID 면 막지 못한다. 그래서 3층은 정의 비교에서
  멈추지 않고 위반을 넣어본다. ADR-013 의 교훈이 그대로 반복됐다.
- 소요: 약 25분

## 2026-11-17 — 계정은 분리했는데 앱은 여전히 슈퍼유저로 붙고 있었다

- 증상: 권한 테스트 21개 중 2개 실패.
  `AssertionError: 설정된 앱 계정 'champs' 이 테이블을 만들 수 있다`
  나머지 19개(각 계정의 거부·허용)는 전부 통과했다.
- 기대 동작: 애플리케이션이 `champs_app` 으로 붙고, 그 계정으로는 DDL 이 안 돼야 한다
- 재현 절차:
  1. `.env` 의 `DATABASE_URL` 을 `postgresql+asyncpg://champs:devpassword@db:5432/champs` 로 되돌린다
  2. `docker compose up -d backend`
  3. `docker compose exec backend uv run pytest tests/integration/test_db_privileges.py`
- 원인: 역할을 만들고(`init_roles.sh`) `.env.example` 을 고쳤지만 **기존 `.env` 는
  그대로였다.** pydantic-settings 는 환경변수를 우선하므로 앱은 계속 예전 URL 을 읽었다.
  즉 **계정 분리는 DB 에만 적용되고 애플리케이션은 아무것도 바뀌지 않은 상태**였다.
- 해결: `.env` 를 갱신했다(백업: `.env.bak`). 그리고 이 상태를 상시 감시하도록
  `TestRuntimeConfiguration` 을 남겼다 — 설정된 계정으로 실제 `CREATE TABLE` 을
  시도해서 거부되는지 본다.
- 배운 것: **"계정을 만들었다"와 "앱이 그 계정을 쓴다"는 다른 사실이다.** 계정별
  권한만 테스트하면 19/21 이 통과하고, 정작 애플리케이션은 슈퍼유저로 돈다.
  ADR-013(상수는 1.0인데 경로는 0초), ADR-014(정의는 있는데 집행은 별개)와 같은
  구조가 세 번째로 반복됐다. 이번엔 **설정 자체를 단정 대상에 넣어** 잡았다.
- 소요: 약 15분

## 2026-11-17 — 실패한 권한 테스트가 개발 DB 에 테이블을 남겼다

- 증상: 위 테스트가 실패한 뒤 개발 DB `public` 스키마에
  `zz_configured_app_should_not_create` 테이블이 남아 있었다
- 기대 동작: 테스트는 통과하든 실패하든 DB 를 원래 상태로 되돌려야 한다
- 재현 절차:
  1. `.env` 의 `DATABASE_URL` 을 부트스트랩 계정으로 되돌린다
  2. `docker compose exec backend uv run pytest tests/integration/test_db_privileges.py -k configured_app`
  3. `docker compose exec -T backend sh -c 'PGPASSWORD=devpassword psql -h db -U champs -d champs -c "\dt zz_*"'`
- 원인: 이 테스트는 **"DDL 이 거부되는가"** 를 본다. 거부되지 않으면 그 `CREATE TABLE`
  은 **성공한다.** 정리 코드를 성공 경로(= 단정 통과 뒤)에만 두면, 단정이 실패하는
  바로 그 경우에 정리가 안 된다. 부작용이 생기는 조건과 정리가 건너뛰어지는 조건이
  **정확히 일치**했다.
- 해결: `try/finally` 로 옮기고, **만든 계정으로** `DROP TABLE IF EXISTS` 를 돌린다
  (다른 계정은 소유자가 아니라 지울 수 없다).
- 배운 것: 부정 테스트에서 "하면 안 되는 일"을 실제로 시도할 때는, **단정이
  실패하는 경우가 곧 부작용이 남는 경우**다. 정리를 `finally` 에 두는 것이
  선택이 아니라 필수다. 이건 리허설에서 쓴 전제 선행 단정과는 다른 축의 함정이다 —
  그쪽은 "검증이 공짜로 통과하는 것", 이쪽은 "검증이 환경을 오염시키는 것".
- 소요: 약 10분

## 2026-11-17 — 리허설은 전부 초록인데 런북에 적은 명령은 동작하지 않았다

- 증상: 런북의 1차 조치 명령이 DB 에 닿지 못한다.
  ```
  init_roles.sh: 대상 — host=<기본값> db=<기본값> user=<기본값>
  psql: error: connection to server on socket "/var/run/postgresql/.s.PGSQL.5432" failed
  ```
  같은 증상이 `backup.sh` 에도 있었다. 그런데 리허설 29개와 권한 테스트 21개는
  전부 통과한다.
- 기대 동작: `docs/RUNBOOK.md` 와 스크립트 머리말에 적힌 명령이 추가 환경변수 없이
  그대로 동작해야 한다. 장애 상황에서 읽는 문서다.
- 재현 절차:
  1. `git stash push -- backend/scripts/backup.sh backend/scripts/restore.sh backend/scripts/init_roles.sh`
  2. `docker compose exec backend bash scripts/init_roles.sh`
  3. `docker compose exec backend bash scripts/backup.sh`
  4. `git stash pop`
- 원인: backend 컨테이너에 `PG*` 가 없다. compose 는 `POSTGRES_USER/PASSWORD/DB` 만
  넘긴다. `PGHOST` 가 비면 libpq 는 **로컬 유닉스 소켓**을 찾고, 이 컨테이너에
  postgres 가 없으니 소켓도 없다.
  테스트가 이걸 못 잡은 이유가 핵심이다 — **리허설 conftest 가 `PGHOST`·`PGUSER`·
  `PGPASSWORD`·`PGDATABASE` 를 전부 명시 주입한다.** 그래서 "아무것도 주지 않았을
  때"는 검증 범위 밖이었다. 내가 검증할 때는 변수를 붙여 돌리고, 문서에는 짧은
  형태를 적었다.
- 해결: 세 스크립트에 libpq 기본값을 넣었다. 기본 계정은 **최소 권한 쪽**으로 골랐다 —
  `backup.sh`→`champs_read`, `restore.sh`→`champs_migrate`,
  `init_roles.sh`→부트스트랩(`POSTGRES_*`, CREATEROLE 이 필요하므로).
  그리고 `tests/rehearsal/test_ops_script_defaults.py` 4개를 추가했다. 계정을
  **일부러 알려주지 않는** 환경(`bare_env()`)으로 돌려서, 동작하는지와 그때 어느
  계정을 쓰는지를 함께 단정한다. 수정 전 스크립트로 4개 전부 실패를 확인했다.
- 덤으로 고친 것: `init_roles.sh` 머리말의 두 번째 사용법이
  `PGHOST=db-rehearsal docker compose exec ...` 였다. 이 형태는 변수가 docker CLI
  에만 걸리고 **컨테이너에는 들어가지 않아** 조용히 개발 DB 로 간다.
  `docker compose exec -e PGHOST=...` 로 바꿨다.
  그리고 런북·scripts/README 의 동작하는 긴 명령들이 **부트스트랩 슈퍼유저**를
  안내하고 있었다. 리허설은 분리 계정으로 도는데 사람이 읽는 문서만 슈퍼유저였다.
- 배운 것: **명시 주입은 테스트를 결정론적으로 만들지만, 기본값을 검증 범위에서
  빼낸다.** 그리고 기본값은 사용자가 실제로 밟는 경로다. ADR-013(상수와 게이트),
  ADR-014(정의와 집행), ADR-015(계정과 설정)에 이어 같은 모양이 네 번째다 —
  **A 를 검증했다고 A 를 쓰는 B 가 검증된 것은 아니다.**
  증빙 문서에 명령을 적을 때는 **적은 그 형태 그대로 한 번 돌려본다.**
- 소요: 약 35분

## 2026-11-17 — 노력치 솔버가 정상 입력에서 2.2초를 쓴다

- 증상: `POST /api/v1/calc/ev-tune` 에 survive 제약 8개(API 허용 최대)를 보내면
  p50 **2,215ms**, 최대 2,509ms. 제약 1개는 15.8ms 다. 응답은 `200` 이고
  답도 맞다 — 느린 것 하나가 문제다.
- 기대 동작: 제약이 늘어도 비용이 선형으로 늘어야 한다. 제약당 약 14ms 였으므로
  8개면 110ms 정도가 예상이었다.
- 재현 절차:
  1. `docker compose up -d backend`
  2. 아래를 실행한다 (제약 개수를 3~8 로 바꿔 곡선을 본다).
     ```bash
     python -c "
     import json,time,urllib.request
     G={'hp':108,'attack':130,'defense':95,'sp_attack':80,'sp_defense':85,'speed':102}
     S={'kind':'survive','opponent':{'attack_stat':200,'special_attack_stat':200,'types':['ice']},
        'move':{'power':50,'type':'ice','category':'physical'}}
     b={'base_stats':G,'types':['dragon','ground'],'nature':'jolly',
        'constraints':[dict(S,label='ice%d'%i) for i in range(8)]}
     r=urllib.request.Request('http://localhost:8000/api/v1/calc/ev-tune',
        data=json.dumps(b).encode(),headers={'Content-Type':'application/json'})
     t=time.perf_counter(); urllib.request.urlopen(r).read()
     print(f'{(time.perf_counter()-t)*1000:.0f} ms')"
     ```
- 원인: `app/domain/ev_solver.py` 의 `_best_combination` 이 제약별 후보 집합의
  **데카르트 곱을 전부 순회한다.** 후보가 제약마다 정확히 5개라 `5^n` 이다.
  실측으로 센 조합 수: 제약 3개 125 / 4개 625 / 6개 15,625 / **8개 390,625**.
  두 가지가 겹쳐 있다.
    1. 예산 초과 가지치기(`if total > MAX_TOTAL_POINTS: continue`)가 조합을
       **만든 뒤**에 있어서 순회 횟수를 전혀 줄이지 못한다.
    2. `_combine` 이 스탯별 `max` 라서, 같은 제약 8개는 39만 조합이 **전부 같은
       답으로 합쳐진다.** 390,624번이 버려지는 계산이다.
- 해결: **아직 고치지 않았다.** 관측성 작업을 먼저 하기로 결정했다 — 대시보드가
  이 지연을 드러내게 만든 뒤 고치면, "관측 장치를 넣었고 그게 사고를 집었다"가
  하나의 이야기가 된다. 당장은 정확성 문제가 아니고 단일 사용자 환경이라
  감당 가능하다. 대신 세 곳에 남겼다.
    - `docs/RUNBOOK.md` 6장 — **2초대는 정상**이라는 판단 기준 (모르면 정상을
      장애로 처리한다)
    - [ADR-016](../adr/016-observability-metrics.md) — 지연 히스토그램 버킷 선택의 근거
    - 지연 경보를 **의도적으로 넣지 않은** 이유
  고칠 때 방향: 열거 중 가지치기(DFS 로 누적 합을 들고 중단) + 중복 제약 제거.
  domain 계산이라 계획·승인·테스트가 필수이고, 답이 바뀌면 엔진 버그다.
- 배운 것: 측정하지 않으면 이런 건 드러나지 않는다. 테스트 654개가 전부
  통과하는데 — **테스트는 "맞는가"를 보고 "얼마나 걸리는가"는 보지 않는다.**
  그리고 입력 상한(`MAX_CONSTRAINTS = 8`)을 정할 때 비용 곡선을 보지 않았다.
  허용하는 입력은 전부 "정상 동작"이므로, 상한은 성능 계약이기도 하다.
- 소요: 약 50분 (측정 스크립트 작성·재측정 포함)

## 2026-11-17 — 기본 히스토그램 버킷이 느린 경로를 전부 한 칸에 몰아넣었다

- 증상: `/metrics` 를 붙인 직후, 경로별 지연 히스토그램의 버킷이
  `0.1 / 0.5 / 1.0 / +Inf` 넷뿐이었다. 솔버의 2.2초 요청이 전부 `+Inf` 로 들어간다.
- 기대 동작: 경로별 `histogram_quantile` 로 p95 를 계산할 수 있어야 한다.
  그게 대시보드 패널 하나의 전부다.
- 재현 절차:
  1. `curl -s http://localhost:8000/metrics | grep -oE 'http_request_duration_seconds_bucket\{handler="[^"]*",le="[^"]*"' | sed 's/.*le="//' | sort -g -u`
  2. 기본 설정이면 `0.1 0.5 1.0 +Inf` 가 나온다.
- 원인: `prometheus-fastapi-instrumentator` 의 `latency_lowr_buckets` 기본값이
  `(0.1, 0.5, 1)` 이다. 고해상도 쪽(`http_request_duration_highr_seconds`)은
  버킷이 21개로 충분하지만 **handler 라벨이 없다.** 즉 기본값으로는
  "경로별이지만 거친 것" 또는 "세밀하지만 전체 합산" 둘 중 하나만 된다.
  1.1초와 60초가 같은 칸에 있으면 p95 는 상한 없는 추정이 된다.
- 해결: 실측 분포에 맞춰 버킷을 직접 줬다.
  `(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 3.0, 5.0)`.
  2.2초 요청이 `le="3.0"` 에 들어가는 것을 확인했다 (`+Inf` 아님).
  `tests/integration/test_metrics_endpoint.py::TestLatencyBuckets` 가 "실측
  최악값이 유한 버킷 사이에 있다" 와 "설정한 버킷이 실제 노출과 일치한다" 를
  단정한다 — 선언과 노출은 다른 사실이다.
- 배운 것: 관측 도구의 기본값은 **일반적인 웹 요청**을 가정한다. 우리 솔버처럼
  초 단위가 정상인 엔드포인트가 있으면 기본값이 조용히 쓸모없어진다.
  더 나쁜 건 **대시보드가 멀쩡해 보인다**는 점이다 — 그래프가 그려지고 숫자가
  나오지만, 정작 느린 하나를 구분하지 못한다. 버킷은 측정한 다음에 정한다.
- 소요: 약 25분

## 2026-11-17 — 읽기 전용 계정으로 익스포터를 돌리니 15초마다 권한 오류가 쌓였다

- 증상: `postgres-exporter` 로그에 스크랩마다 한 줄씩.
  ```
  level=ERROR source=collector.go:201 msg="collector failed" name=wal
    err="pq: permission denied for function pg_ls_waldir"
  ```
  지표 수집 자체는 되고 있었다 (`pg_up=1`).
- 기대 동작: 익스포터가 오류 없이 돌아야 한다. 평소에 에러가 쌓이면 진짜
  문제가 생겼을 때 로그에서 구분할 수 없다.
- 재현 절차:
  1. `docker compose --profile observability up -d postgres-exporter`
  2. `docker compose logs postgres-exporter | grep -iE 'error|denied'`
- 원인: `wal` 콜렉터가 `pg_ls_waldir()` 를 호출하고, 그 함수는 `pg_monitor`
  역할을 요구한다. 익스포터는 `champs_read` 로 도는데 그 계정은
  `pg_read_all_data` 만 가진다 (ADR-015).
- 해결: **계정을 넓히는 대신 콜렉터를 껐다** (`--no-collector.wal`).
  선택지가 둘인데 방향이 반대였다.
    - `champs_read` 에 `pg_monitor` 부여 → 읽기 계정의 권한이 넓어진다.
      ADR-015 의 되돌리는 조건("읽기 계정의 용도가 갈리면")에 해당한다.
    - 콜렉터 끄기 → WAL 지표를 잃는다.
  로컬에 복제 구성이 없어 WAL 지표를 대시보드에 쓰지 않는다. **쓰지도 않는
  지표 때문에 권한을 넓히지 않는다.** 끈 뒤 반복 오류가 사라졌고
  `pg_up=1`·`pg_stat_database`·`pg_stat_user_tables` 는 그대로 수집된다.
- 배운 것: 도구가 요구하는 권한은 **그 도구가 할 수 있는 일 전부**의 합집합이고,
  우리가 쓰는 부분집합과 다르다. 기본 설정을 그대로 쓰면 최소 권한이 조용히
  무너진다 — 가장 쉬운 해결책이 늘 `GRANT` 를 더 주는 쪽이기 때문이다.
  먼저 물어볼 것은 "**그 지표를 쓰기는 하나**" 다.
- 소요: 약 20분

## 2026-11-18 — 솔버가 유효한 입력 하나로 워커를 영구 점유할 수 있었다

- 증상: `POST /api/v1/calc/ev-tune` 가 제약 8개에서 p50 2,215ms. 응답은 정상
  (`feasible: true`) 이고 오류도 없다. 제약 수를 늘릴 때 시간이 선형이 아니다 —
  4개 46ms → 7개 457ms → 8개 2,268ms 로 **제약 하나에 5배씩** 늘었다.
- 기대 동작: 제약 8개는 스키마가 허용하는 정상 입력이다 (`MAX_CONSTRAINTS = 8`).
  유효한 입력이 수 초를 쓰면 안 되고, 하물며 돌아오지 않으면 안 된다.
- 재현 절차:
  ```bash
  # 제약 n개 요청을 만들어 응답 시간을 잰다. n 을 1,4,7,8 로 바꿔가며 돌린다.
  # (전선 5개 입력 — 한카리아스에게 냉동펀치급 물리 기술)
  python - <<'EOF'
  import json, time, urllib.request
  n = 8
  body = {
      "base_stats": {"hp":108,"attack":130,"defense":95,
                     "sp_attack":80,"sp_defense":85,"speed":102},
      "types": ["dragon","ground"],
      "constraints": [
          {"kind":"survive","label":"w%d" % i,
           "opponent":{"attack_stat":200,"special_attack_stat":200,"types":["ice"]},
           "move":{"power":50,"type":"ice","category":"physical"}}
          for i in range(n)],
  }
  req = urllib.request.Request("http://localhost:8000/api/v1/calc/ev-tune",
      data=json.dumps(body).encode(), headers={"Content-Type":"application/json"})
  t = time.perf_counter()
  with urllib.request.urlopen(req) as r:
      out = json.load(r)
  print("%.1fms" % ((time.perf_counter()-t)*1000), out.get("feasible", True))
  EOF
  ```
  최악 입력은 위 스크립트에서 `base_stats` 를
  `{"hp":60,"attack":100,"defense":70,"sp_attack":100,"sp_defense":70,"speed":100}`,
  `types` 를 `["normal"]`, 상대 공격을 290, 기술을 `power 80 / type normal` 로
  바꾼 것이다 (파레토 전선 23개). **변경 전 코드로는 돌리지 말 것** — 돌아오지 않는다.
- 원인: `_best_combination()` 이 제약별 후보 집합의 **데카르트 곱을 전수 순회**한다.
  예산 초과 검사(`if total > MAX_TOTAL_POINTS: continue`)가 조합을 **만든 뒤** 에
  있어서 순회 횟수를 줄이지 못한다. 계측한 조합 수는 정확히 `후보수^제약수` 였다
  (8제약 = 390,625).

  ★ **그리고 2.2초는 최악값이 아니라 표본 하나였다.** "후보 5개"는 그 입력의
  파레토 전선 크기고, 전선은 입력에 따라 변한다. 종족값·위력·상대 공격을 훑어
  최댓값을 찾으니 **23개** 였다 (HP종족 60 / 방어종족 70 / 위력 80 / 상대공격 290).
  `23^8 = 78,310,985,281` 조합 × 리프당 5.8μs = **약 126시간**.
  스키마를 통과하는 유효한 요청 하나로 워커를 영구 점유할 수 있었다.
- 해결: 결합 단계를 **분기한정**으로 바꿨다 ([ADR-017](../adr/017-constraint-combination-search.md)).
  결합이 스탯별 `max` 라 부분 조합의 총합이 단조 증가하므로, 중간에 상한을 넘으면
  그 아래를 버릴 수 있다. 각 집합의 최소 총합 후보를 합친 값을 **씨앗 상한**으로
  써서 첫 리프를 기다리지 않고 1단계부터 자른다.
  제약 8개 2,215ms → **96ms**, 전선 23개 최악 입력은 126시간(추정) → **66ms**,
  탐색 노드 506개. 전수 조합 오라클과 무작위 250건 대조해 답이 동일함을 확인했다.
- 배운 것 (셋):
  1. **"측정했다"가 "최악값을 측정했다"는 아니다.** 어제 경보 임계치를 정하려고
     이 엔드포인트를 쟀고, 2.2초를 런북에 "정상"으로 적었다. 표본 하나였고
     실제 최악은 그 20만 배였다. 입력이 **크기를 가진 자료구조를 만드는** 코드는
     그 크기의 분포를 봐야 한다 — 호출 횟수만 세면 안 된다.
  2. **값이 같다고 자료구조가 같은 게 아니다.** 증분 병합을 처음 짰을 때
     `0 > 0` 이 거짓이라 `{SPEED: 0}` 키를 생략했다. 배분과 총합은 같은데
     딕셔너리가 달라서 차등 대조가 379건 어긋났다. 전수 탐색과 대조하지 않고
     기존 테스트만 돌렸다면 전부 통과했을 것이다 (실제로 474개 전부 통과했다).
  3. **가지치기 품질과 성능 사이에 완만한 구간이 없다.** RED 검증으로 상한을
     12포인트만 느슨하게 했더니 테스트가 10분을 넘겨 중단해야 했다.
- 소요: 약 2시간 30분 (측정 1시간, 구현·검증 1시간, 기록 30분)

## 2026-11-18 — 메타 API 테스트가 단독 실행에서는 통과하고 전체 실행에서만 깨졌다

- 증상: `tests/integration/test_meta_api.py` 가 단독으로는 21개 전부 통과하는데,
  `pytest` 전체 실행에서는 13개가 **setup 단계에서** 에러로 끝났다.
  ```
  ERROR at setup of TestUsageList.test_entries_are_ranked_in_order
  RuntimeError: Event loop is closed
  ```
- 기대 동작: 테스트 결과는 실행 순서에 의존하지 않아야 한다. 단독으로 초록인데
  전체에서 빨간 테스트는 **둘 중 어느 쪽이 진실인지 알 수 없게** 만든다.
- 재현 절차:
  ```bash
  # 단독 — 통과한다
  docker compose exec -T backend uv run pytest tests/integration/test_meta_api.py

  # 전체 — 같은 테스트가 깨진다
  docker compose exec -T backend uv run pytest
  ```
- 원인: 두 가지가 겹쳤다.
  1. `TestClient(app)` 를 `with` 없이 쓰면 **요청마다 새 이벤트 루프**가 돈다.
     앞 요청이 만든 비동기 DB 커넥션은 이미 닫힌 루프에 묶여 있다.
  2. 엔진이 `@lru_cache` 로 **프로세스당 하나**다 (`app/db/session.py`).
     운영에서는 옳다 — 요청마다 만들면 커넥션 풀이 매번 새로 생긴다.
     그런데 테스트에서는 앞선 모듈이 **자기 루프에서** 엔진을 먼저 쓰고,
     풀에 남은 커넥션이 그 **닫힌 루프에 묶인 채** 다음 모듈로 넘어온다.

  1번만 고쳤을 때 단독 실행이 통과해서 다 된 줄 알았다. 2번은 **앞선 모듈이
  있을 때만** 발현하므로 단독 실행으로는 영원히 안 보인다.
- 해결: 테스트 픽스처에서 `with TestClient(app)` 로 루프를 하나로 묶고,
  `get_engine.cache_clear()` / `get_session_factory.cache_clear()` 로 이 모듈의
  루프에서 엔진을 새로 만들게 했다. 모듈이 끝나면 다시 비워 다음 모듈도 같게 한다.
  **운영 코드는 건드리지 않았다** — 운영에는 루프가 하나뿐이라 이 문제가 없고,
  테스트 격리를 위해 운영 구조를 바꾸는 건 거꾸로다.
- 배운 것: **"단독으로 돌려서 통과"는 통과가 아니다.** 프로세스 전역 상태
  (`lru_cache`, 싱글턴, 모듈 변수)가 있으면 테스트 결과가 실행 순서에 의존하고,
  그 의존은 파일 하나만 돌릴 때 숨는다. 새 통합 테스트는 **반드시 전체 실행으로도**
  확인한다.
- 소요: 약 25분

## 2026-11-18 — 같은 이름의 응답 모델이 OpenAPI 에서 모듈 경로로 뭉개졌다

- 증상: 프론트 타입 생성 후 타입체크가 실패했다.
  ```
  error TS2339: Property 'OptionOut' does not exist on type
    '{ ...; app__schemas__meta__SpreadOut: ...; }'
  ```
- 기대 동작: 생성된 타입에 `OptionOut` / `SpreadOut` 이 그 이름으로 있어야 한다.
- 재현 절차:
  ```bash
  docker compose exec -T backend uv run python -m scripts.dump_openapi
  python -c "import json; d=json.load(open('backend/openapi.json', encoding='utf-8')); \
    print([k for k in d['components']['schemas'] if 'Option' in k or 'Spread' in k])"
  ```
- 원인: `app/schemas/ev_tune.py` 와 `app/schemas/meta.py` 에 **같은 이름**의
  Pydantic 모델(`OptionOut`, `SpreadOut`)이 있었다. FastAPI 는 이름이 충돌하면
  **모듈 경로를 붙여** 구분한다 (`app__schemas__meta__SpreadOut`).
  오류가 아니라 자동 처리라 백엔드 쪽에서는 아무 신호도 없었다.
- 해결: 메타 쪽을 `MetaOptionOut` / `MetaSpreadOut` 으로 바꿨다. 스키마를
  재생성하니 이름이 그대로 나온다.
- 배운 것: **내부 모듈 경로가 공개 API 계약에 새어 나갈 수 있다.** 모델 이름은
  파일 안에서만 유일하면 되는 게 아니라 **애플리케이션 전체에서** 유일해야 한다.
  그리고 이걸 알려준 건 백엔드가 아니라 **프론트 타입체크**였다 — 타입 생성을
  붙이지 않았으면 계약이 그 모양인 줄도 몰랐을 것이다.
- 소요: 약 10분

## 2026-11-18 — 내 머신에서만 통과하는 테스트를 PR 에 올렸다

- 증상: 로컬에서 686 passed 로 전부 초록이던 상태로 PR 을 올렸는데 CI 가
  **13 error / 2 failed** 로 깨졌다. 전부 메타 API 테스트고 메시지가 하나다.
  ```
  AssertionError: {"detail":"'Singles' 포맷의 활성 스냅샷이 없다"}
  assert 404 == 200
  ```
- 기대 동작: 테스트는 **환경에 남아 있는 데이터에 기대지 않아야** 한다.
  깨끗한 DB 에서도 같은 결과가 나와야 한다.
- 재현 절차:
  ```bash
  # CI 와 같은 환경을 손으로 만든다 — 스키마와 시드만 있고 수집은 없는 DB
  docker compose exec -T -e PGPASSWORD="$POSTGRES_PASSWORD" db \
    psql -U champs -d postgres -c "CREATE DATABASE champs_citest OWNER champs_migrate;"
  docker compose exec -T -e PGDATABASE=champs_citest backend bash scripts/init_roles.sh
  docker compose exec -T \
    -e MIGRATION_DATABASE_URL="postgresql+asyncpg://champs_migrate:PW@db:5432/champs_citest" \
    -e DATABASE_URL="postgresql+asyncpg://champs_app:PW@db:5432/champs_citest" \
    backend sh -c 'uv run alembic upgrade head && uv run python -m scripts.load_seed_to_db'
  docker compose exec -T \
    -e DATABASE_URL="postgresql+asyncpg://champs_app:PW@db:5432/champs_citest" \
    backend uv run pytest
  ```
- 원인: 테스트가 **개발 DB 에 이미 있던 활성 스냅샷**을 읽고 있었다.
  내 DB 에는 11월 14일에 수집한 실제 스냅샷이 있어서 전부 통과했다.
  CI 는 깨끗한 Postgres 에 `alembic upgrade` → 시드 적재까지만 하고
  **수집은 돌지 않는다** — 돌면 안 된다. 외부 API 를 CI 에서 부르지 않는 것이
  이 프로젝트의 규칙이다. 그래서 활성 스냅샷이 없고 모든 요청이 404 였다.

  ★ 테스트 독스트링에 "**개발 DB 의 활성 스냅샷을 쓴다**"고 내가 직접
  적어놓고 올렸다. 의존 사실을 알면서도 그게 **다른 환경에서는 없다는 것**을
  연결하지 못했다.
- 해결: 픽스처가 스냅샷을 직접 만든다.
  - `ingest()` 를 통과시킨다. 직접 INSERT 하면 JSONB 구조가 테스트에만 있는
    모양으로 굳고, 수집 쪽이 바뀌어도 이 테스트는 모른다.
  - **커밋하지 않는다.** `app.dependency_overrides[get_session]` 로 API 가
    테스트와 같은 세션을 쓰게 하고 끝나면 롤백한다. 커밋하면 개발 DB 의
    활성 스냅샷과 부분 유니크 인덱스에서 충돌하고, 남으면 화면이 테스트
    데이터를 보여준다.
  - "활성 스냅샷이 없으면 404" 테스트를 추가했다. **CI 를 깨뜨린 그 상황인데
    정작 그 경로에 테스트가 없었다.**
- 배운 것: **"내 머신에서 통과"는 통과가 아니다.** 같은 날 "단독 실행 통과는
  통과가 아니다"(이벤트 루프 격리)를 배우고 JOURNAL 에 적었는데, 한 단계 위의
  같은 실수를 바로 저질렀다. 격리의 층이 여러 개다 —
  **테스트 간 격리 → 실행 순서 독립 → 환경 독립.** 통합 테스트를 새로 쓰면
  "이 테스트가 기대는 데이터를 누가 만들었나"를 묻고, 답이 "내 DB 에 있던 것"
  이면 그건 테스트가 아직 완성되지 않은 것이다.
- 소요: 약 50분 (원인 파악 10분, 재작성 25분, 깨끗한 DB 재현·검증 15분)

## 2026-11-18 — 규칙 문서가 지킬 수 없는 규칙을 적고 있었다

- 증상: `CLAUDE.md` 6장이 "예외는 `app/core/exceptions.py` 에 정의한 도메인 예외를
  쓴다. 맨 `Exception` 금지" 인데, 코드는 전혀 그렇지 않았다.
  - `app/core/exceptions.py` 에는 **예외 정의가 하나도 없다** (핸들러 등록 모듈이다)
  - 도메인 예외 14개는 `app/domain/exceptions.py` 에 있다
  - 도메인 밖 예외 5개는 맨 `Exception` 을 상속하고 있었다
- 기대 동작: 규칙 문서와 코드가 같은 것을 말해야 한다. 다르면 다음 사람이
  어느 쪽을 믿어야 할지 모르고, 둘 중 하나는 반드시 틀린 상태로 남는다.
- 재현 절차:
  ```bash
  grep -n "예외는" CLAUDE.md
  grep -rn "class .*(Exception):" backend/app/ --include=*.py
  grep -c "class " backend/app/core/exceptions.py   # 예외 정의가 없다
  ```
- 원인: ★ **규칙끼리 모순이었다.** `CLAUDE.md` 1-5 가 "`app/domain/` 은 아무것도
  import 하지 않는다"고 못 박는다. 그러면 도메인 예외를 `core/` 에 두는 순간
  도메인이 `core` 를 import 하게 되므로 **구조적으로 불가능**하다.
  코드가 `domain/exceptions.py` 로 간 것은 게으름이 아니라 **옳은 판단**이었고,
  문서가 그걸 따라오지 못한 것이다.

  그 사이에 도메인 밖 예외가 하나씩 쌓이면서 공통 베이스 없이 늘어났다.
  이름은 `~Error` 로 끝나서 린터(N818)는 통과했지만, "수집 중 난 오류 전부"를
  한 번에 잡을 수 있는 타입이 없었다.
- 해결: 계층마다 베이스를 두는 것으로 **문서를 현실에 맞추고, 현실의 빈 곳을 채웠다.**
  - `IngestionError`, `ServiceError` 를 새로 만들고 기존 5개를 그 아래로
  - 라우터가 개별 예외 대신 `NotAvailableError`(베이스)를 잡게 바꿨다
  - `CLAUDE.md` 6장을 계층별 표로 고치고, **왜 한 곳에 못 모으는지**를 적었다
  - ★ `tests/unit/test_exception_layering.py` 를 추가했다. `app` 패키지를 전부
    import 해서 정의된 예외를 모으고, 계층 베이스를 상속하지 않는 것이 하나라도
    있으면 깨진다. 고의 위반 클래스를 넣어 RED 확인했다.
- 배운 것: **문서만 고치면 같은 일이 또 생긴다.** 문서는 읽는 사람에게만
  작동하고, 테스트는 읽지 않는 사람에게도 작동한다. 그리고 코드가 문서를
  어기고 있을 때 **먼저 의심할 것은 코드가 아니라 문서**다 — 이번엔 문서가
  물리적으로 불가능한 것을 요구하고 있었다.
- 소요: 약 40분

## 2026-11-18 — 운영 이미지를 한 번도 띄워본 적이 없었다

- 증상: 테스트 698개가 초록이고 CI 도 초록인데, **운영 이미지로는 마이그레이션을
  돌릴 수 없는 상태**였다. 배포를 시작하려고 처음 띄워보고 알았다.
  ```
  $ docker run ... --target prod ...
  [ERROR] Control server error: [Errno 13] Permission denied: '/home/appuser'
  $ docker exec prodtest ls /app
  app  data          # ← alembic 도 alembic.ini 도 scripts 도 없다
  ```
- 기대 동작: 운영 이미지는 배포 절차가 필요로 하는 것을 전부 들고 있어야 하고,
  기동 로그가 깨끗해야 한다.
- 재현 절차:
  ```bash
  docker build --target prod -t pokehelper-backend:prodtest ./backend
  docker run --rm -d --name prodtest -p 8099:8000 pokehelper-backend:prodtest
  docker logs prodtest | grep -i error
  docker exec prodtest ls /app
  docker rm -f prodtest
  ```
- 원인: 두 가지가 겹쳤다.
  1. `Dockerfile` prod 스테이지에 **"Phase 2 에서 alembic 도입 시 아래 두 줄을
     되살린다"** 는 주석과 주석 처리된 COPY 두 줄이 있었다. **Phase 2 가 끝났는데
     주석은 그대로였다.** 미래의 나에게 남긴 메모는 알림이 아니다.
  2. `adduser --no-create-home` 이라 `/home/appuser` 가 없는데, gunicorn 23 의
     control server 가 거기에 쓰려고 한다. 서비스는 정상 동작해서 **응답만 보면
     아무 문제가 없어 보인다.**

  ★ 공통 원인은 하나다 — **단위·통합 테스트는 전부 dev 이미지에서 돈다.**
  dev 는 소스를 마운트하고 개발 의존성을 포함한다. 그래서 운영 이미지에만
  있는 결함은 어떤 테스트도 보지 않는다. "빌드 성공"도 못 잡는다 —
  **빌드는 둘 다 성공했다.**
- 해결: 이미지를 고치고, **CI 에 `prod-image` 잡을 추가**했다.
  - `alembic` / `alembic.ini` / `scripts` 를 COPY 하고 홈 디렉토리를 만든다
  - CI 가 운영 이미지를 빌드해 **띄우고, 헬스체크하고, 기동 로그에
    `[ERROR]`·`Traceback`·`Permission denied` 가 없는지 본다**
  - 배포에 필요한 파일 존재, 개발 의존성 미포함(`pytest` 없음), 비루트 실행도 단정
  - 두 수정을 각각 되돌려 CI 검사가 실제로 잡는 것을 확인했다 (RED → GREEN)
- 배운 것: **테스트가 도는 이미지와 배포되는 이미지가 다르면, 테스트는 배포에
  대해 아무것도 말해주지 않는다.** 커버리지 90%도 운영 이미지의 빠진 파일을
  잡지 못한다. 그리고 "나중에 되살린다"는 주석은 **아무도 알려주지 않는다** —
  되살려야 할 조건을 아는 것은 주석이 아니라 테스트뿐이다.
- 소요: 약 1시간

## 2026-11-18 — 보호장치를 켜자 기존 테스트 33개가 깨졌다

- 증상: 속도 제한과 `/metrics` 토큰을 붙이자 통과하던 테스트가 세 묶음으로 깨졌다.
  ```
  31 failed  tests/integration/test_calc_ev_tune.py   assert 429 == 422
   9 errors  tests/integration/test_metrics_endpoint.py (401)
   2 failed  tests/integration/test_alert_rules.py    "지표가 전부 없다"
  ```
- 기대 동작: 보호장치를 켜도 기존 테스트는 **자기가 검증하던 것**을 계속 검증해야
  한다. 깨진다면 테스트가 보호장치를 모르고 있었다는 뜻이다.
- 재현 절차:
  ```bash
  # 제한을 끈 상태 (기존)
  docker compose exec -T backend uv run pytest tests/integration/test_calc_ev_tune.py
  # 제한을 켠 상태
  docker compose exec -T -e RATE_LIMIT_CALC_REQUESTS=20 backend \
    uv run pytest tests/integration/test_calc_ev_tune.py
  ```
- 원인: 세 가지가 각각 달랐다.
  1. **속도 제한 누적** — `TestClient` 는 전부 같은 클라이언트로 보인다. 한 모듈의
     요청이 한 바구니에 쌓여 20건을 넘겼다.
  2. **`/metrics` 401** — 테스트가 토큰 없이 호출했다.
  3. ★ **교차검증이 401 을 "지표가 없다"로 읽었다** — 가장 고약하다.
     `exposed_metrics` 가 응답 본문만 보고 상태 코드를 안 봤다. 401 이면 본문이
     비고, 비면 "참조하는 지표가 전부 없다"가 된다. **거짓 경보였다.**
     그리고 같은 맹점이 반대로도 작동한다 — 참조 쪽도 비면 **공짜로 통과**한다.
- 해결:
  - 속도 제한은 **끄지 않았다.** 끄면 라우터에 의존성이 붙어 있는지조차 보지
    못하고 "정의했지만 적용되지 않은" 상태를 놓친다. 대신 전역 conftest 에서
    테스트마다 카운터를 비운다 (테스트 하나 = 클라이언트 하나).
  - `/metrics` 를 쓰는 테스트는 설정에서 토큰을 읽어 **조건부로 붙인다.**
    토큰이 없는 환경에서도 그대로 돈다.
  - 교차검증 픽스처에 **`status_code == 200` 단정**을 넣었다. 읽기에 실패한
    것과 지표가 없는 것을 구분한다.
- 배운 것: **보호장치를 켤 때 깨지는 테스트는 버그 신호다.** 끄고 통과시키면
  그 보호장치는 테스트 밖으로 나가고, 그 순간 "붙어 있는지"를 아무도 확인하지
  않는다. 그리고 ★ **빈 응답을 "값이 없음"으로 읽는 코드는 전부 의심해야 한다** —
  PromQL 이 없는 지표를 빈 결과로 주는 것과 같은 함정이 테스트 안에도 있었다.
- 소요: 약 35분

## 2026-11-18 — compose 의 `:?` 가드가 무관한 명령까지 전부 막았다

- 증상: Grafana 기본 비밀번호(`admin/admin`)를 없애려고 `${GRAFANA_ADMIN_USER:?...}`
  를 넣었더니, Grafana 와 아무 상관 없는 명령이 전부 실패했다.
  ```
  $ docker compose exec -T backend sh -c 'echo hi'
  error while interpolating services.grafana.environment.GF_SECURITY_ADMIN_USER:
    required variable GRAFANA_ADMIN_USER is missing a value
  ```
- 기대 동작: Grafana 를 띄울 때만 막혀야 한다. `exec backend` 는 Grafana 를
  쓰지 않는다.
- 재현 절차:
  ```bash
  # docker-compose.yml 의 grafana 환경변수를 ${GRAFANA_ADMIN_USER:?...} 로 바꾼 뒤
  unset GRAFANA_ADMIN_USER
  docker compose exec -T backend sh -c 'echo hi'   # 실패한다
  ```
- 원인: compose 의 변수 보간은 **파일 전체에서 한 번에** 일어난다. 서비스를
  고르거나 프로필을 거는 것과 무관하게, 파일 안의 모든 `${...}` 가 평가된다.
  `grafana` 가 `observability` 프로필 뒤에 있어도 소용없다.
- 해결: 로컬 기본값(`:-admin`)으로 되돌렸다. 로컬 Grafana 는 포트가 localhost
  에만 열리고, 운영 자격증명은 SSM 에서 주입한다 (ADR-019). **가드를 없앤 게
  아니라 자리를 옮긴 것**이다 — 강제는 배포 경로에 둔다.
- 배운 것: 가드를 넣을 때 **그 가드가 언제 평가되는지**를 봐야 한다. "필요할 때만
  막는다"고 생각한 장치가 실제로는 **항상** 평가되면, 막으려던 것보다 더 많은
  것을 막는다. 그리고 로컬 개발을 막는 가드는 결국 우회된다.
- 소요: 약 10분

## 2026-11-19 — 새 테스트가 아무것도 검사하지 않은 채 통과했다

- 증상: 보안그룹 테스트 8개 중 5개가 통과하고 3개가 실패했다. 실패한 셋이
  **전부 "대상이 없다"** 였다.
  ```
  5 passed
  FAILED ... ::test_the_check_is_not_vacuous
  FAILED ... ::test_the_private_route_table_has_no_default_route - 프라이빗 라우트 테이블이 없다
  FAILED ... ::test_public_subnets_do_not_auto_assign           - 퍼블릭 서브넷 선언이 없다
  ```
- 기대 동작: `.tf` 를 파싱해 보안그룹 규칙을 찾고, ALB 가 아닌 보안그룹이
  `0.0.0.0/0` 에 열려 있으면 실패해야 한다.
- 재현 절차:
  ```bash
  docker compose up -d backend
  docker compose exec -T backend uv run pytest \
    tests/integration/test_terraform_security_groups.py -v
  ```
- 원인: **python-hcl2 7.x 가 블록 라벨과 문자열 값에 따옴표를 남긴다.**
  `aws_subnet` 이 아니라 `"aws_subnet"`, `0.0.0.0/0` 이 아니라 `"0.0.0.0/0"`
  으로 나온다. 리소스 타입 비교가 전부 빗나가 **모든 조회 결과가 빈
  딕셔너리**였다.
- 해결: 라벨과 문자열 값의 따옴표를 벗기는 `unquote()` 를 넣었다. 따옴표가
  없어도 그대로 통과하도록 **멱등**으로 만들었다 — 파서 버전에 따라 동작이
  달라지는 것에 테스트를 묶지 않기 위해서다. 파서가 끼워 넣는 메타 키
  (`__is_block__`·`__comments__`)도 함께 걷어낸다.
- ★ 검증: 고친 뒤 **세 가지 변이를 넣어 빨갛게 되는 것을 확인**했다.
  ① RDS 를 `0.0.0.0/0` 에 연다 → 2건 실패
  ② 프라이빗 라우트 테이블에 NAT 경로를 넣는다 → 1건 실패
  ③ 인라인 `ingress {}` 블록으로 우회한다 → 1건 실패
- 배운 것: ★ **빈 결과를 "위반이 없다"로 읽는 검사는 공짜로 통과한다.**
  전제 선행 단정(`test_the_check_is_not_vacuous`)이 없었다면 **"보안그룹을
  검사하는 테스트 8개가 전부 초록"** 인 상태로 머지됐을 것이고, 그걸 믿고
  퍼블릭 서브넷에 배포했을 것이다. 바로 전날 `test_alert_rules.py` 가 401
  응답을 "지표가 전부 없다"로 읽었던 것과 **같은 함정**이다 (ADR-019 결과).
  같은 실수를 이틀 연속 다른 모양으로 했다는 뜻이고, 그래서 이 단정은
  관례가 아니라 **필수**다.
- 소요: 약 20분

## 2026-11-19 — 운영 compose 의 DB 가 첫 기동에서 죽었다

- 증상: `docker-compose.prod.yml` 을 처음 띄우자 DB 가 initdb 중에 종료되고
  나머지가 전부 따라 죽었다.
  ```
  dependency failed to start: container pokehelper-prod-db-1 exited (1)
  db-1 | /docker-entrypoint-initdb.d/init_roles.sh: SQL 파일이 없다: /app/sql/roles.sql
  ```
- 기대 동작: 개발 스택과 똑같이 최소 권한 계정 세 개(ADR-015)가 만들어지고
  DB 가 healthy 가 돼야 한다.
- 재현 절차:
  ```bash
  docker compose -f docker-compose.prod.yml down -v
  docker compose -f docker-compose.prod.yml up -d --build
  docker compose -f docker-compose.prod.yml logs db
  ```
- 원인: initdb 훅으로 `backend/scripts/init_roles.sh` 를 마운트했는데, 그건
  **컨테이너 안에서 손으로 돌리는 용도**라 `/app/sql/roles.sql` 을 찾는다.
  개발 compose 는 전용 훅(`backend/sql/initdb-roles.sh`)과 SQL 을 **따로**
  마운트하고 있었고, 그 이유가 바로 위 줄 주석에 적혀 있었다 — SQL 을 initdb
  디렉토리에 두면 엔트리포인트가 `-v` 변수 없이 한 번 더 실행해서 실패한다.
  **이름이 비슷해서 스크립트만 보고 가져왔고, 옆의 주석을 읽지 않았다.**
- 해결: 개발 compose 와 같은 두 줄을 그대로 쓴다. 비밀번호 환경변수
  (`CHAMPS_*_PASSWORD`)도 같이 빠져 있었다.
- 배운 것: ★ **파일을 쓰고 돌려보지 않았으면 몰랐다.** `docker-compose.prod.yml`
  은 CLAUDE.md 3장에 **이름만 있고 존재하지 않던 파일**이고, 운영 이미지를
  한 번도 돌려본 적이 없어 결함 두 개를 뒤늦게 발견한 것과 같은 종류다
  (ADR-019 맥락). 설정 파일은 "작성했다"가 "동작한다"가 아니다.
  그리고 **비슷한 이름의 스크립트를 복사할 때는 옆 주석을 먼저 읽는다** —
  그 주석은 과거의 누군가가 같은 자리에서 넘어졌다는 표시다.
- 소요: 약 15분

## 2026-11-19 — ARM 빌드가 x86 보다 6~14배 느렸다

- 결정: ARM(`linux/arm64`) 채택은 유지하되, **CI 에서 ARM 을 매번 빌드하지
  않는다.** Dockerfile·의존성 파일이 바뀐 PR 과 주 1회 스케줄에만 돌린다.
- 측정 (로컬, 캐시 없음):

  | 이미지 | x86 | arm64 (QEMU) | 배율 |
  |---|---|---|---|
  | 백엔드 | 11.5초 | 2분 45초 | 14배 |
  | 프론트 | 1분 10초 | 7분 47초 | 6.6배 |

- 판단: ARM 으로 아끼는 돈은 월 $6.96 인데 PR 마다 9분이 더 든다. 매번 돌릴
  값이 아니다. 그렇다고 아예 안 돌리면 배포 당일에 안다.
- 범위를 좁힌 근거: **ARM 고유 실패는 베이스 이미지와 의존성에서만 나온다.**
  aarch64 휠이 없는 패키지, 아키텍처별 바이너리가 원인이다. 애플리케이션
  코드가 바뀐다고 ARM 에서만 깨지지 않는다.
- ★ 빌드 성공만으로 끝내지 않았다. `docker image inspect --format
  '{{.Architecture}}'` 로 **정말 arm64 인지** 단정한다. `--platform` 이
  무시되거나 캐시가 섞이면 x86 이미지가 나오고, 그러면 "ARM 에서 빌드된다"를
  검증했다고 믿으면서 아무것도 검증하지 않은 상태가 된다.
- 남은 것: 네이티브 arm64 러너(`ubuntu-24.04-arm`)면 에뮬레이션이 사라진다.
  퍼블릭 저장소면 무료다. 러너 가용성을 확인하지 않아 지금은 QEMU 다.

## 2026-11-19 — roles.sql 이 RDS 에서는 통째로 실패했을 것이다

- 증상: 배포를 앞두고 `backend/sql/roles.sql` 을 **슈퍼유저가 아닌 계정**으로
  돌려봤더니 세 군데에서 거부당했다. 로컬에서는 한 번도 본 적 없는 오류다.
  ```
  ERROR:  permission denied to alter role
  DETAIL:  Only roles with the SUPERUSER attribute may change the SUPERUSER attribute.
  ERROR:  permission denied to grant role "pg_read_all_data"
  ERROR:  permission denied to change default privileges
  ```
- 기대 동작: ADR-015 의 계정 분리가 **RDS 에서도** 그대로 서야 한다.
  RDS 마스터 사용자는 슈퍼유저가 아니므로, roles.sql 은 슈퍼유저 없이 돌아야 한다.
- 재현 절차:
  ```bash
  docker rm -f rdsim 2>/dev/null
  docker run -d --name rdsim -e POSTGRES_PASSWORD=boot postgres:16-alpine
  docker exec -i rdsim psql -U postgres <<'SQL'
  CREATE ROLE rdsmaster LOGIN PASSWORD 'm' CREATEROLE CREATEDB NOSUPERUSER;
  CREATE DATABASE champsrds OWNER rdsmaster;
  SQL
  docker cp backend/sql/roles.sql rdsim:/tmp/roles.sql
  docker exec -e PGPASSWORD=m rdsim psql -U rdsmaster -h 127.0.0.1 -d champsrds \
    -v migrate_password=m1 -v app_password=a1 -v read_password=r1 -f /tmp/roles.sql
  ```
- 원인: 세 가지가 각각 슈퍼유저 전용이었다.
  1. `ALTER ROLE ... NOSUPERUSER NOREPLICATION NOBYPASSRLS` — 이 세 속성은
     **슈퍼유저만** 바꿀 수 있다. 값이 이미 꺼져 있어도 마찬가지다.
  2. `GRANT pg_read_all_data TO champs_read` — 미리 정의된 역할을 부여하려면
     그 역할에 대한 **ADMIN 옵션**이 필요하다.
  3. ★ `ALTER DEFAULT PRIVILEGES FOR ROLE champs_migrate` — 그 역할에 대한
     **USAGE 멤버십**이 필요하다. PG16 은 CREATEROLE 역할이 만든 역할에 대해
     **ADMIN 은 주되 INHERIT 는 끈 채로** 멤버십을 준다. 그래서 "내가 만든
     역할인데 그 역할의 기본 권한을 못 바꾸는" 상태가 된다.
- 해결:
  - 1번은 **강제할 수 있으면 강제하고, 없으면 검증**한다. 검증에서 특권이
    발견되면 조용히 넘어가지 않고 `RAISE EXCEPTION` 으로 멈춘다.
  - 3번은 앞에 `GRANT champs_migrate TO CURRENT_USER WITH INHERIT TRUE` 를
    조건부로 넣는다.
  - 2번은 **대체 경로**를 둔다. 못 받으면 `GRANT SELECT ON ALL ...` +
    `ALTER DEFAULT PRIVILEGES ... TO champs_read` 로 같은 보장을 만든다.
    대체 경로는 champs_migrate 가 만든 테이블만 덮으므로 WARNING 을 남긴다.
  - ★ 그리고 **로컬도 비슈퍼유저로 통일**했다. `master-role.sql` 이
    `champs_master`(NOSUPERUSER CREATEROLE CREATEDB)를 만들고, roles.sql 은
    그 역할로 돈다. 로컬·CI·RDS 가 같은 경로를 탄다.
- ★ 3번이 가장 위험했다. **배포 당일에는 멀쩡히 돌고**, 기존 테이블에는 권한이
  있으니 아무 증상이 없다. **다음 마이그레이션이 만든 테이블부터** 앱이 못
  읽는다. 시차 때문에 원인을 마이그레이션에서 찾게 된다 — 정작 원인은 몇 주 전
  배포다. 그 블록의 주석이 "이 파일에서 가장 틀리기 쉬운 부분"이라고 적어두고
  있었는데, 정말 그랬다.
- 배운 것: ★ **"로컬에서 된다"는 로컬의 권한으로 된다는 뜻이다.** 로컬
  부트스트랩 계정이 슈퍼유저라서, 슈퍼유저 전용 구문이 섞여도 아무도 몰랐다.
  ci.yml 이 DB 계정에 대해 같은 원칙을 이미 적어두고 있었다 — "CI 도 로컬과
  같은 계정으로 돌려야 권한 때문에 운영에서만 깨지는 상황을 CI 가 잡는다."
  그 원칙이 **부트스트랩 계정에는 적용되지 않고 있었다.**
- 소요: 약 70분

## 2026-11-19 — 같은 경로 문제를 세 번째로 냈다

- 증상: PR 에서 CI 만 빨갛다. 로컬 743개는 전부 통과한다.
  ```
  FAILED tests/integration/test_db_privileges.py::
         TestTheScriptRunsWithoutSuperuser::test_roles_sql_succeeds_without_superuser
  psql: error: /app/sql/roles.sql: No such file or directory
  ```
- 기대 동작: 테스트가 컨테이너에서도 CI 러너에서도 `roles.sql` 을 찾아야 한다.
- 재현 절차:
  ```bash
  # 컨테이너 밖(= CI 와 같은 상황)에서 후보 경로를 확인한다
  python -c "
  from pathlib import Path
  f = Path('backend/tests/integration/test_db_privileges.py').resolve()
  for c in [Path('/app/sql/roles.sql'), f.parents[2]/'sql'/'roles.sql']:
      print(('O' if c.is_file() else 'X'), c)"
  ```
- 원인: 기본값에 `/app/sql/roles.sql` 을 박았다. compose 는 저장소를 `/app` 에
  마운트하지만 **CI 러너에는 `/app` 이 없다.** `ROLES_SQL` 환경변수를 쓰도록
  했지만, CI 는 그것을 "DB 계정 생성" 단계에만 두고 pytest 단계에는 두지
  않는다 — 부트스트랩 자격증명을 모든 단계가 들고 돌지 않게 하려는 **의도된**
  설정이다 (ci.yml 주석).
- 해결: 후보 경로 목록으로 찾는다. 못 찾으면 건너뛰지 않고 `pytest.fail` 한다.
  ```python
  _ROLES_SQL_CANDIDATES = (
      Path("/app/sql/roles.sql"),                                  # 컨테이너
      Path(__file__).resolve().parents[2] / "sql" / "roles.sql",   # CI
  )
  ```
- 배운 것: ★ **같은 해법을 두 번 쓰고도 세 번째에 적용하지 않았다.**
  `test_alert_rules.py` 가 `/opt/observability` 와 저장소 경로를 후보로 두고,
  `test_terraform_security_groups.py` 가 `/opt/terraform` 으로 같은 일을 한다.
  둘 다 내가 쓴 것인데 세 번째에서 기본값을 박았다. **패턴을 아는 것과 그
  패턴을 쓰는 것은 다르다.**
  그리고 **로컬 전체 통과는 CI 통과가 아니다** — 컨테이너에 있는 경로가
  러너에는 없다. 경로를 문자열로 박는 순간 환경 의존이 생긴다.
- 남은 것: 세 파일이 각자 후보 목록을 들고 있다. 공용 헬퍼로 모으면 네 번째를
  막을 수 있다. 이번 PR 범위 밖이라 하지 않았다.
- 소요: 약 10분

## 2026-11-21 — PowerShell 이 terraform 인자를 쪼갰다

- 증상: 문서대로 쳤는데 terraform 이 **있지도 않은 위치 인자**를 받았다고 거부했다.
  ```
  PS> terraform init -backend-config=backend.hcl
  Error: No positional arguments are expected
  The init command does not expect any positional arguments. Did you mean to use -chdir?
  ```
- 기대 동작: `-backend-config=backend.hcl` 은 Terraform 공식 문서의 표준 표기다.
  백엔드 설정을 읽고 S3 백엔드로 초기화해야 한다.
- 재현 절차:
  ```powershell
  # 빈 디렉터리에서. AWS 를 건드리지 않는다.
  mkdir argtest; cd argtest
  terraform init -backend-config=backend.hcl       # 실패
  terraform init "-backend-config=backend.hcl"     # 성공
  terraform init -backend-config backend.hcl       # 성공
  ```
- 원인: **Windows PowerShell 5.1** 이 외부 프로그램에 인자를 넘길 때,
  **`-` 로 시작하면서 `=` 가 들어간 인자**를 쪼갠다. terraform 은 그 결과
  `backend.hcl` 을 위치 인자로 받고 거부한다. **terraform 의 문제가 아니라
  셸의 문제**다 — Git Bash·cmd·PowerShell 7 에서는 그냥 된다.
- 해결: 인자 전체를 따옴표로 묶는다 — `terraform init "-backend-config=backend.hcl"`.
  공백으로 분리해도 된다.
- ★ **왜 bootstrap 에서는 안 터졌나**: 거기서는
  `terraform apply -var state_bucket_name=...` 를 썼다. `=` 가 **`-` 로 시작하지
  않는 뒤쪽 토큰**에 있어서 쪼갤 대상이 아니었다. 같은 세션, 같은 셸에서
  하나는 되고 하나는 안 되니 셸을 의심하기까지 시간이 걸렸다.
- 배운 것: ★ **명령이 "문서대로"인데 실패하면 명령이 아니라 셸을 의심한다.**
  에러 메시지가 terraform 에서 나왔다고 원인이 terraform 인 것은 아니다.
  terraform 은 **자기가 받은 인자**를 정직하게 보고했을 뿐이고, 그 인자를
  바꾼 건 셸이다. 그리고 **빈 디렉터리에서 재현**한 것이 결정적이었다 —
  AWS 를 건드리지 않고 인자 파싱만 분리해 확인할 수 있었다.
- 소요: 약 10분

## 2026-11-21 — plan 은 통과하고 apply 가 400 으로 터졌다 (한글 설명)

- 증상: `terraform apply` 가 자원을 만들다가 보안그룹 4개에서 멈췄다.
  ```
  Error: creating Security Group (pokehelper-prod-alb): api error InvalidParameterValue:
  Value (ALB. 인터넷에서 직접 받는 유일한 보안그룹) for parameter GroupDescription
  is invalid. Character sets beyond ASCII are not supported.
  ```
- 기대 동작: `validate` 와 `plan` 이 통과했으면 `apply` 도 끝까지 가야 한다.
- 재현 절차:
  ```bash
  # .tf 의 security group description 에 한글을 넣고
  terraform validate   # 통과한다
  terraform plan       # 통과한다
  terraform apply      # 여기서 400
  ```
- 원인: EC2 의 `GroupDescription` 과 보안그룹 규칙 `Description` 은 **ASCII
  전용**이다. 문법도 타입도 길이도 맞아서 로컬 검증은 전부 통과하고,
  **AWS 에 실제 요청을 보내는 순간에만** 거부된다.
- 해결: 설명 13개를 영문으로 바꿨다. **주석은 한글 그대로** 뒀다 — 읽는
  사람을 위한 것이고 AWS 로 가지 않는다. 그 경계를
  `TestDescriptionsAreAscii` 가 집행한다.
- ★ **검사를 둘로 나눴다.** ASCII 여부와 **허용 문자 집합**은 다른 검사다.
  AWS 가 받는 기호는 `._-:/()#,@[]+=&;{}!$*` 뿐이라 `>` `<` `"` `%` 는
  ASCII 여도 거부된다. 화살표를 `->` 로 쓰고 싶어지는 자리라 특히 걸린다.
  변이로 확인했다 — 한글은 둘 다 실패, `->` 는 **허용문자 검사만** 실패.
- ★ **절반만 만들어진 인프라가 남았다.** VPC·서브넷·IGW·라우팅·ECR 15개는
  생성되고 보안그룹부터 멈췄다. 과금 자원이 없어 피해는 없었지만, **RDS 나
  ALB 단계에서 같은 일이 나면 돈이 걸린 채로 중단된다.**
  ADR-021 이 "처음부터 권한을 좁히면 apply 가 중간에 죽어 절반만 만들어진
  인프라가 남는다"고 적어둔 것과 **같은 사고가 권한이 아니라 문자셋으로** 났다.
- 배운 것: ★ **`plan` 이 통과했다는 것은 "Terraform 이 이해했다"일 뿐이다.**
  AWS 가 받아줄지는 별개 사실이다. 로컬에서 끝나는 검증(`validate`·`plan`)과
  원격이 판정하는 검증은 다른 층이고, 그 사이에 끼는 제약(문자셋·이름 규칙·
  리전별 지원 여부)은 **테스트로 당겨와야** `apply` 중단을 막을 수 있다.
- 부수 피해: Docker 가 꺼져 있어 `ruff` 를 못 돌린 채 테스트를 커밋해서
  **CI 가 E501 로 한 번 더 빨개졌다.** "테스트를 실행하지 못했다"고 적었지만
  **lint 도 같이 못 돌았다는 것을 짚지 않았다.** 못 돌린 검사는 전부 나열한다.
- 소요: 약 25분

## 2026-11-22 — PowerShell 스크립트가 세 가지로 연달아 깨졌다

- 증상: ECR 푸시 스크립트를 쓰고 `-DryRun` 으로 돌리는데 **원인이 다른 실패가
  세 번 연달아** 났다.
  ```
  1) 줄 105: Missing closing ')' in expression.
  2) 줄 187: Unexpected token '?몄떆' in expression or statement.
  3) docker : WARNING: daemon is not using the default seccomp profile
     + CategoryInfo : NotSpecified: (...) [], RemoteException
  ```
- 기대 동작: `-DryRun` 은 아무것도 실행하지 않고 할 일만 출력해야 한다.
- 재현 절차:
  ```powershell
  # 구문만 확인 (실행하지 않는다)
  $e = $null
  [System.Management.Automation.PSParser]::Tokenize(
      (Get-Content .\infra\scripts\push-images.ps1 -Raw), [ref]$e) > $null
  $e | ForEach-Object { "$($_.Token.StartLine): $($_.Message)" }
  ```
- 원인: 셋 다 **PowerShell 5.1 고유의 것**이고 서로 무관했다.
  1. **`Die @"` — 함수 인자 자리의 here-string.** `@` 를 splat 으로 읽는다.
     변수에 먼저 받아 넘기면 된다.
  2. ★ **BOM 없는 `.ps1` 은 ANSI(cp949)로 읽힌다.** 한글이 깨지고 here-string
     종료자까지 어긋나 엉뚱한 구문 오류가 난다. **UTF-8 BOM 으로 저장**해야 한다.
     이건 토큰 검사에서만 나는 문제가 아니라 **실제 실행에서도 깨진다.**
  3. ★ **네이티브 명령의 stderr 를 리다이렉트하면 ErrorRecord 가 된다.**
     `docker info *> $null` 에서 docker 가 경고 한 줄을 내자
     `$ErrorActionPreference = "Stop"` 과 맞물려 스크립트가 그 자리에서 죽었다.
     출력을 받아야 하는 네이티브 호출만 `Invoke-Native` 로 감쌌다 — 그 안에서만
     `Continue` 로 내리고 `$LASTEXITCODE` 를 직접 본다.
- 덤으로: 파라미터 이름을 `$Profile` 로 지었는데 **PowerShell 자동 변수**다.
  동작은 하지만 가리는 것이라 `$AwsProfile` 로 바꿨다.
- ★ **처음 만든 `-DryRun` 이 dry run 이 아니었다.** 전제 확인 단계에서
  `aws sts get-caller-identity` 를 부르고 있었다. "아무것도 안 한다"고 해놓고
  계정에 요청을 보내는 것이라, 사용자가 걸어둔 규칙(AWS 호출은 먼저 묻는다)에도
  걸렸다. 도구 권한 검사가 그걸 막아줬다 — **내가 못 지킨 규칙을 도구가 잡았다.**
- 배운 것: 셸 스크립트는 **"쓴 것"과 "그 셸이 읽는 것"이 다를 수 있다.**
  인코딩·인자 파싱·스트림 처리가 전부 셸에 달려 있고, 그 셋이 각각 다른 모양으로
  깨진다. `-DryRun` 같은 **실행하지 않는 경로를 먼저 만들어** 돌려본 것이
  빌드 10분을 쓰기 전에 전부 잡아낸 이유다.
- 소요: 약 30분

## 2026-11-22 — 실행 안내에서 작업 디렉터리를 빼먹었다 (두 번째)

- 증상: `.\infra\scripts\push-images.ps1 -DryRun` 이
  `CommandNotFoundException` 으로 떨어졌다.
- 기대 동작: 스크립트가 실행돼 빌드·푸시 계획을 출력해야 한다.
- 재현 절차:
  ```powershell
  cd C:\project\pokehelper\infra\terraform
  .\infra\scripts\push-images.ps1 -DryRun     # ❌ CommandNotFoundException
  cd C:\project\pokehelper
  .\infra\scripts\push-images.ps1 -DryRun     # ✅
  ```
- 원인: 스크립트가 아니라 **안내가 틀렸다.** 내가 준 경로는 저장소 루트 기준인데,
  `infra/terraform/README.md` 의 2~3절이 `cd bootstrap` / `cd ..` 로 그 디렉터리에
  서 있게 만든다. 거기서 이어 치면 경로가 한 단계 어긋난다.
- 해결: README 5-B 블록 첫 줄에 `cd ..\..` 를 넣고, 다른 절과 기준 디렉터리가
  다르다는 것을 명시했다.
- ★ **같은 종류의 두 번째다.** 앞서 `terraform plan` 안내에서도 `cd` 를 빼먹어
  `Error: No configuration files` 가 났다. 한 번은 실수지만 두 번은 패턴이다 —
  **명령 블록은 "무엇을 치는가"만 적고 "어디서 치는가"를 빠뜨리기 쉽다.**
  앞으로 실행 안내는 디렉터리를 포함한 채로 준다.
- 배운 것: 스크립트는 내부에서 `$PSScriptRoot` 로 루트를 찾아 `Set-Location` 하므로
  **어디서 불러도 빌드 자체는 올바르다.** 즉 이건 코드 결함이 아니라 **문서 결함**
  이었고, 그래서 테스트가 잡을 수 없었다. 문서에만 있는 절차는 문서로만 지켜진다.
- 소요: 5분

## 2026-11-23 — ECR docker login 이 400 으로 떨어졌다 (stdin 의 BOM)

- 증상: `push-images.ps1` 의 ECR 로그인 단계에서
  `Error response from daemon: login attempt to https://<계정ID>.dkr.ecr.ap-northeast-2.amazonaws.com/v2/
  failed with status: 400 Bad Request`. 토큰 발급은 성공했다(`$LASTEXITCODE` 0).
- 기대 동작: `Login Succeeded` 가 떠서 빌드·푸시로 넘어가야 한다.
- 재현 절차:
  ```powershell
  # 원인 확인만 한다. AWS 를 건드리지 않는다.
  chcp                                              # 65001 이면 재현된다
  "$([Console]::InputEncoding.GetType().Name) / BOM $([Console]::InputEncoding.GetPreamble().Length) 바이트"
  # -> UTF8Encoding / BOM 3 바이트

  # 실제 재현 (AWS 호출 2건: 토큰 발급 + 로그인)
  $acct = aws sts get-caller-identity --profile pokehelper --query Account --output text
  $reg  = "$acct.dkr.ecr.ap-northeast-2.amazonaws.com"
  $tok  = aws ecr get-login-password --region ap-northeast-2 --profile pokehelper
  $tok | docker login --username AWS --password-stdin $reg      # 400
  [Console]::InputEncoding = New-Object System.Text.UTF8Encoding $false
  $tok | docker login --username AWS --password-stdin $reg      # Login Succeeded
  ```
- 원인: 코드 페이지가 **65001(UTF-8)** 이면 PS 5.1 이 외부 프로그램 stdin 에
  UTF-8 BOM `EF BB BF` 를 먼저 쓴다. `docker login --password-stdin` 은
  **뒤쪽 개행만 떼고 앞쪽 BOM 은 떼지 않는다.** 그래서 비밀번호가 BOM 으로
  시작한 채 ECR 에 가고, 401(권한 없음)이 아니라 **400(요청이 잘못됐다)** 이
  돌아온다. 이 400/401 차이가 "권한이 아니라 바이트 문제"라는 유일한 단서였다.
- 해결: 로그인 직전에 `[Console]::InputEncoding` 을 BOM 없는 UTF-8 로 바꾸고
  `finally` 에서 되돌린다. 콘솔이 없는 호스트에서 setter 가 던지면 `cmd` 파이프로
  내려간다 — cmd 의 파이프는 BOM 을 붙이지 않는다.
- ★ **배운 것 1 — 나는 엉뚱한 변수를 재고 "아니다"라고 결론 냈다.**
  BOM 을 의심한 건 맞았는데 `$OutputEncoding` 을 쟀다. `ASCIIEncoding / BOM 0 바이트`
  가 나오자 가설을 기각했다. 하지만 BOM 은 `[Console]::InputEncoding` 에서 온다.
  실측 표가 그걸 분리해 보여준다 — 65001 에서 `$OutputEncoding` 만 고쳐도
  **여전히 400** 이고, `InputEncoding` 을 고쳐야 통과한다.
  **"측정했는데 음성"은 "원인이 아니다"가 아니다. 측정 대상이 틀렸을 수 있다.**
  전제 선행 단정(TEST_STRATEGY 3-9)을 테스트에만 적용하고 디버깅에는 적용하지
  않았다 — 음성 결과를 받았을 때 "이 측정이 양성을 낼 수는 있었나"를 묻지 않았다.
- ★ **배운 것 2 — 원래 주석이 반쪽짜리였다.** 그 자리에 "PowerShell 의 파이프는
  인코딩을 섞을 수 있다. 변수에 받아 넘긴다"고 적어뒀었다. 위험은 맞게 봤는데
  대책이 틀렸다. **변수에 받아도 파이프를 나갈 때 인코딩이 다시 걸린다.**
  맞는 걱정에 틀린 대책을 달아두면, 다음 사람(나)이 그 지점을 이미 막힌 곳으로
  읽고 지나친다. 실제로 그렇게 지나쳤다.
- ★ **배운 것 3 — 테스트로 막을 수 없는 종류다.** 셸의 콘솔 인코딩과 외부
  프로세스 stdin 의 상호작용이라, `.tf` 테스트처럼 소스를 읽어 잡을 수 없고
  실제 레지스트리가 필요하다. 대신 **스크립트 안에 원인과 우회를 주석으로
  박고**, 실패 메시지가 직접 재현 명령을 출력하게 했다.
- 덤으로 확인된 것: aws-cli 2.32.2~2.32.5 에 같은 400 을 내는 회귀가 있었고
  (aws/aws-cli#9864) **2.32.6 에서 고쳐졌다.** 로컬은 2.41.2 라 해당 없음.
  버전 회귀를 먼저 의심했다면 엉뚱한 데서 시간을 썼을 것이다.
- 출처: https://dev.classmethod.jp/en/articles/powershell-ecr-docker-login-400-bom/
- 소요: 약 25분

## 2026-11-23 — BOM 을 넘기니 자격증명 저장이 터졌다 (docker login 을 버렸다)

- 증상: BOM 수정 후 다시 로그인하자 400 은 사라지고 다음 오류가 났다.
  `Error saving credentials: error storing credentials - err: exit status 1,
   out: 'The stub received bad data.'`
- 기대 동작: `Login Succeeded` 가 떠서 `docker push` 가 그 자격증명을 쓸 수 있어야 한다.
- 재현 절차:
  ```powershell
  # 전제: credsStore 가 desktop(또는 wincred)이고 ECR 토큰이 2.5KB 를 넘는다
  Get-Content "$env:USERPROFILE\.docker\config.json" -Raw   # credsStore 확인
  $tok.Length                                               # 3072 자

  [Console]::InputEncoding = New-Object System.Text.UTF8Encoding $false
  $tok | docker login --username AWS --password-stdin $reg   # 인증은 통과, 저장에서 실패
  ```
- 원인: **Windows 자격증명 관리자는 비밀 하나를 약 2.5KB 까지만 저장한다.**
  ECR 토큰은 3000자대라 넘치고, `docker-credential-desktop`/`wincred` 가
  RPC 스텁 오류(`The stub received bad data`)를 낸다. ECR 이 토큰 길이를 늘린
  뒤로 Windows 에서 흔해졌다.
  docker/docker-credential-helpers#346 · 같은 저장소 PR #482 · aws/aws-cdk#32925
- 해결: **`docker login` 자체를 버렸다.** `~/.docker/config.json` 의
  `credHelpers` 에 레지스트리 → `ecr-login` 을 등록하면, `docker push` 가 매번
  헬퍼를 호출해 AWS 자격증명으로 토큰을 받아 넘긴다. **토큰을 저장하지 않으므로**
  2.5KB 한계도, stdin BOM 도, 12시간 만료도 전부 사라진다.
  `docker-credential-ecr-login` 은 Docker Desktop 이 함께 설치한다(0.8.0, SSO 지원).
- ★ **배운 것 1 — 오류 메시지가 바뀐 것은 진전이다.** 400 → 저장 실패로 바뀐 건
  "인증은 통과했다"는 뜻이었다. 같은 작업이 또 실패한 것처럼 보이지만 **실패
  지점이 뒤로 밀렸다.** 증상이 바뀌었는지 먼저 보는 게 수정이 들었는지 판단하는
  가장 빠른 길이었다.
- ★ **배운 것 2 — 두 번 우회한 경로는 버릴 때가 됐다는 신호였다.** ①BOM ②2.5KB
  한계를 각각 우회할 수도 있었지만, 둘 다 `docker login` 이 **토큰을 받아 저장
  한다**는 구조에서 나왔다. 저장하지 않는 경로로 바꾸니 둘이 같이 사라졌다.
  우회를 쌓는 대신 **우회가 모이는 지점을 의심**해야 했다.
- ★ **배운 것 3 — 내가 만든 안내문이 또 가린 값을 복사용 블록에 넣었다.**
  헬퍼 미설정 시 출력하는 안내문에 `$registry` 를 박았더니, `-DryRun` 에서는
  그게 `<계정ID>...` 플레이스홀더로 찍혔다. **S3 버킷 이름으로 이미 한 번 한
  실수다**(JOURNAL 2026-11-21). 안내문이 계정 ID 를 명령으로 받아 조립하게
  고쳤다. 가린 값은 산문에만 쓰고, 복사용 블록에는 **절대** 넣지 않는다.
- 소요: 약 20분

## 2026-11-23 — IAM 정책을 관리형 대신 직접 썼다 (그리고 테스트가 읽을 수 있게 썼다)

- 맥락: `iam.tf` 의 실행 역할에 AWS 관리형 `AmazonECSTaskExecutionRolePolicy`
  를 붙일지, 같은 권한을 인라인으로 직접 쓸지.
- 선택지:
  - **관리형** — 두 줄로 끝난다. 대신 ECR·CloudWatch Logs 를 `Resource: "*"`
    로 연다. 지금 계정에 우리 자원밖에 없어 **차이가 보이지 않는다** — 그건
    "좁다"가 아니라 "넓은데 들어갈 곳이 없다"일 뿐이다.
  - **직접 작성** — ECR 은 우리 리포지토리 2개로, 로그는 `/ecs/pokehelper-prod*`
    로 한정된다. 대신 빠뜨린 액션이 있으면 **태스크가 기동에 실패**한다.
- 결정: 직접 작성. ADR-021 맥락이 "콘솔 마법사는 관리형 정책을 자동으로 붙여
  무엇이 왜 붙었는지 모른 채 돌아간다"를 문제로 적었고, 관리형을 붙이면 그걸
  Terraform 에서 반복하는 셈이 된다.
- 근거: 검토 결과 Claude 제안에 동의.
- ★ 부수 효과로 **쓰는 방식이 테스트 가능성을 결정했다.** 정책을
  `jsonencode({...})` 로 쓰면 python-hcl2 가 그 전체를 **문자열 하나**로
  돌려줘서 액션·자원·조건을 따로 볼 수 없다. "PassRole 의 Resource 가 `*` 인가"
  를 단정할 방법이 없어진다. `data "aws_iam_policy_document"` 로 쓰면
  `statement` 블록이 HCL 그대로 파싱돼 구조를 단정할 수 있다. 그래서
  `terraform_support.py` 에 `data_sources()` 를 더하고 정책을 data 소스로 썼다.
  **"테스트가 읽을 수 있는 형태로 쓴다"가 구현 형태를 고른 기준이었다.**
- 검증: 돌연변이 7건을 넣어 전부 탐지되는 것을 확인했다 — PassRole 에 `*` 추가 /
  `iam:PassedToService` 조건 제거 / `managed_policy_arns` 로 Admin 부착 /
  `logs:CreateLogGroup` 추가 / 스케줄러 신뢰 정책에서 `aws:SourceAccount` 제거 /
  `*` statement 에 pull 액션 섞기 / 실행 역할에 Secrets Manager 읽기 추가.
  **이 파일의 단정은 거의 전부 "없음"을 확인하는 형태**라 파싱이 빈 결과를
  돌려주면 전부 초록이 된다. 그래서 전제 선행 단정을 따로 뒀다.
- 소요: 약 70분

## 2026-11-23 — README 가 지킬 수 없는 약속을 먼저 적어두고 있었다

- 맥락: `secrets.tf` 를 쓰기 직전. README 0장이 **"시크릿 값은 Terraform 에
  넣지 않는다 — SSM 파라미터는 Terraform 이 만들기만 하고 값은 따로 넣는다"**
  를 이미 적고 있었고, 어제 세운 계획도 `ignore_changes = [value]` 를 전제했다.
- 확인한 것: `ignore_changes` 는 **plan 시점의 동작만** 바꾼다. `refresh` 는
  실제 객체에 맞춰 state 를 다시 쓰므로, 손으로 넣은 값이 다음 refresh 에서
  **state 에 평문으로 들어온다.** state 버킷은 버전 관리까지 켜져 있어서 구
  버전에 영구히 남는다. 프로바이더 이슈 #3475 가 아직 열려 있다.
- 조치: 저장소를 Secrets Manager 로 바꿨다 (ADR-022). 시크릿 껍데기만 Terraform
  이 만들고 `aws_secretsmanager_secret_version` 을 쓰지 않으면 값이 어디에도
  저장되지 않는다. 월 $0.40.
- ★ **배운 것 1 — 문서가 코드보다 먼저 약속하면 그 약속이 검증되지 않은 채
  남는다.** README 0장은 `secrets.tf` 가 생기기 전에 쓰였고, 그래서 **아무도
  틀렸다고 말해주지 않았다.** CLAUDE.md 12-3 이 금지하는 "하지 않은 작업을
  증빙에 쓰는 것"의 변종이다 — 지어낸 게 아니라 **확인하지 않은 것**을 적었다.
- ★ **배운 것 2 — "구현 전에 전제를 확인한다"가 처음으로 값을 치렀다.** 그냥
  썼으면 돌아갔고 테스트도 초록이었다. 틀린 건 **문서의 주장**이라 실행으로는
  안 드러난다. 이번엔 `secrets.tf` 를 쓰기 전에 멈췄는데, 멈춘 이유는 README
  문장이 "state 에 평문으로 남는다"를 너무 단정적으로 적고 있어서였다.
  **단정적인 문장이 의심의 단서였다.**
- ★ **배운 것 3 — 보호 장치가 반대로 작동할 수 있다.** state 버킷의 버전 관리는
  state 가 깨졌을 때 되돌릴 유일한 수단이라 켠 것인데, 시크릿이 들어가는
  순간 **지우기를 어렵게 만드는 쪽**으로 작동한다.
- 소요: 약 40분 (확인 15분 + 설계 변경 25분)

## 2026-11-23 — 운영 DB 계정을 만들 수단이 아예 없었다 (ECS 를 만들기 전에 발견)

- 증상: 아직 나지 않았다. **ECS 를 다 만들고 태스크를 띄운 다음에** 터질
  것이었다 — "시크릿은 들어갔는데 DB 인증이 실패한다". 오늘 남은 작업을
  브리핑하려고 `/health` 200 까지의 경로를 되짚다가 발견했다.
- 기대 동작: ADR-015 의 세 계정(`champs_migrate`·`champs_app`·`champs_read`)을
  운영 DB 에도 만들 수 있어야 한다. 그 계정이 없으면 `DATABASE_URL` 을 만들 수
  없고 앱이 뜨지 않는다.
- 재현 절차: 이미 ECR 에 올라간 이미지에서 확인된다.

  ```bash
  docker run --rm --entrypoint sh \
    <계정ID>.dkr.ecr.ap-northeast-2.amazonaws.com/pokehelper/backend:a5fa6b99a4f1 \
    -c 'command -v psql || echo "psql: NOT FOUND"; ls /app/sql; ls /app'
  ```

  ```
  psql: NOT FOUND
  ls: cannot access '/app/sql': No such file or directory
  alembic  alembic.ini  app  data  scripts
  ```

- 원인: **두 개의 결정이 각각은 맞는데 겹치면 길이 막힌다.**
  1. `data.tf` — RDS 는 프라이빗 서브넷 + `publicly_accessible = false`,
     보안그룹은 **태스크 SG 에서만** 인바운드 허용. NAT 도 배스천도 없다.
     → 운영 DB 에 닿을 수 있는 곳은 **ECS 태스크 안**뿐이다.
  2. `backend/Dockerfile` — prod 스테이지는 `app`·`data`·`alembic`·`scripts`
     만 복사하고, DB 클라이언트를 **의도적으로 뺐다**. 주석에 이유까지 적혀
     있었다: *"운영 백업은 관리형(RDS 스냅샷)이고, 운영 이미지에 DB
     클라이언트를 두면 공격 표면만 늘어난다."*

  ★ **틀린 건 2의 판단이 아니라 전제였다.** 운영 DB 에 필요한 작업을 *백업*으로
  만 봤고, **계정 생성**을 빼놓았다. 그 둘은 같은 도구를 쓴다.
- 선택지:
  - **A: prod 에 `postgresql-client-16` + `COPY ./sql ./sql`** — 두 줄. ECS
    Exec 으로 바로 돌릴 수 있다. 대신 운영 이미지가 DB 도구를 들고 간다.
  - B: `admin` 빌드 스테이지를 따로 만들어 별도 태그로 푸시 — 운영 런타임은
    깨끗하지만 스테이지·태그·푸시 스크립트가 늘어난다.
  - C: 임시로 `publicly_accessible = true` + 내 IP 만 허용 → 로컬 실행 →
    되돌리기 — 이미지 변경이 없지만 `test_terraform_data.py` 가 깨지고(테스트가
    제 일을 하는 것) 운영 DB 를 인터넷에 여는 창이 생긴다.
- 결정: A.
- 근거: 검토 결과 Claude 제안에 동의.
- ★ **공격 표면 논거는 이 경우에 약하다.** 운영 컨테이너는 이미 `asyncpg` 와
  `DATABASE_URL` 을 둘 다 들고 있다. 컨테이너를 잡은 쪽에게 `psql` 이 더해주는
  능력은 사실상 없다. `roles.sql` 도 비밀을 담지 않는 SQL 텍스트다. 일반론으로는
  맞는 논거가 **이 구성에서는 값을 거의 못 치른다.**
- 조치:
  - `dbclient` 스테이지를 새로 만들고 `dev`·`prod` 가 둘 다 상속한다. 복사해
    두면 한쪽만 고쳐질 때 메이저 버전이 갈라지는데, **그게 2026-11-17 에 복구
    리허설이 잡은 결함과 같은 종류다**(pg_restore 17 → 서버 16).
  - 원래 주석을 **지우지 않고** 인용한 뒤 왜 뒤집었는지와 되돌리는 조건을 붙였다.
  - `infra/terraform/README.md` 4장의 순서를 고쳤다 — 시크릿 값 주입이 4단계에
    적혀 있었는데, DSN 세 개가 **RDS 엔드포인트 호스트명**을 품어서 RDS 를
    만들기 전에는 채울 수 없다. 5단계 안쪽 순서가 고정된다.
- 검증: prod 이미지를 로컬에서 빌드해 `psql 16.16`(서버 메이저와 일치)과
  `/app/sql/` 3개 파일이 `appuser` 소유로 들어간 것을 확인했다. dev 이미지도
  재빌드해 테스트를 돌렸다. **크기는 332MB → 378MB (+46MB)** — 처음에 "~15MB"
  로 추정했는데 3배였다. `postgresql-client-16` 의 의존성을 과소평가했다.
- ★ 배운 것: **"필요 없다"고 적은 주석은 그 시점의 요구사항 목록이다.** 요구가
  하나 늘면 주석은 그대로 틀린 말이 되는데, 주석은 틀렸다고 말해주지 않는다.
  오늘로 두 번째다 (바로 앞 항목의 README 0장과 같은 모양이다).
- 소요: 약 55분

## 2026-11-23 — dns.tf·alb.tf 를 쓰면서 ADR 이 안 정해둔 것 셋을 정했다

- 맥락: ADR-020 이 구성도까지 정해뒀다 — 퍼블릭 서브넷의 ALB 하나, 경로 기반
  라우팅(`/api/*` → 백엔드, `/*` → 프론트), Grafana 는 백엔드 태스크의 사이드카.
  그런데 코드를 쓰다 보니 **구성도에 없는 틈이 셋** 있었다. 셋 다 "나중에 왜
  이렇게 했더라"가 나올 것이라 적어둔다.
- ① **`/health` 를 백엔드 규칙에 넣었다.** FastAPI 의 헬스 라우터는 접두사가
  없어서 경로가 `/health` 다(`app/api/v1/health.py`). `/api/*` 만 백엔드로 보내면
  `/health` 는 프론트로 가 404 가 되고, **README 4장의 수락 기준인 "`/health` 가
  200 인가"를 외부에서 확인할 수 없다.** 공개해도 되는 이유는 응답이
  `status`·`service`·`version` 뿐이라서다. 다만 `version`("0.1.0")은 노출된다.
  - ★ ALB **헬스체크**는 리스너 규칙을 거치지 않고 타깃에 직접 간다. 즉 규칙이
    없어도 ALB 는 정상 판정을 한다. 막히는 건 **사람이 눈으로 확인하는 것**뿐이다.
    그래서 처음에는 넣을 이유가 없어 보였다.
- ② **`/metrics` 는 일부러 라우팅하지 않았다.** 경로가 `/metrics` 라 `/api/*` 에
  걸리지 않고 프론트로 가 404 가 된다. ADR-019 의 `METRICS_TOKEN` 은 그 위의
  두 번째 겹이 된다 — **토큰이 유일한 방어선이 아니게 하는 것**이 목적이다.
  Prometheus 사이드카는 같은 태스크 안에서 localhost 로 긁으므로 손실이 없다.
  테스트가 이걸 집행한다(`test_metrics_is_not_routed_from_the_internet`).
- ③ **Grafana 경로를 `/grafana` 로 정했다.** ADR-020 구성도에는 Grafana 포트
  (3001)만 있고 경로가 없다. `security_groups.tf` 에 "ALB to Grafana sidecar"
  규칙이 이미 있어서 **ALB 를 거치는 것은 결정돼 있었고, 이름만 비어 있었다.**
  - ★ `/grafana` 와 `/grafana/*` 를 **둘 다** 적어야 한다. ALB 경로 패턴에서
    `/grafana/*` 는 슬래시 없는 `/grafana` 에 매칭되지 않는다. 하나만 적으면
    "슬래시를 붙이면 되는데 안 붙이면 404" 가 된다.
  - ★ `ecs.tf` 가 같이 맞춰야 한다 — `GF_SERVER_ROOT_URL` 과
    `GF_SERVER_SERVE_FROM_SUB_PATH=true`. 빠뜨리면 로그인 화면은 뜨는데
    리다이렉트가 루트로 가서 프론트로 떨어진다.
- 그 밖에 기본값과 다르게 둔 것과 이유:
  - `deregistration_delay = 30` (기본 300) — 기본값이면 배포 한 번에 5분이 그냥
    지나간다. 우리 요청은 수백 ms 다.
  - `matcher = "200"` — 넓히면 "500 을 돌려주면서 정상"이 가능해진다.
  - `drop_invalid_header_fields = true` — 기본값이 아니다. 끄면 위조 헤더 해석이
    뒤의 프레임워크에 달린다.
  - `enable_deletion_protection = false` — ★ RDS 와 **다른** 판단이다. ALB 는
    상태가 없어 몇 분이면 다시 만들 수 있고, 바뀌는 DNS 이름은 alias 가 따라간다.
    모든 자원에 보호를 걸면 보호가 의식에서 지워진다.
  - 액세스 로그 끔 — S3 버킷과 요청당 저장 비용이 붙는다. ADR-020 결정 5 가 로그
    비용을 줄이는 쪽을 골랐다. **되돌리는 조건: 접근 패턴 조사가 필요해지면 켠다.**
  - ACM 검증 대기 타임아웃 `30m` (기본 75분) — 문서상 상한이 30분이다. 75분을
    기다리는 것은 고장을 늦게 아는 것뿐이다. **기다리는 시간이 진단 신호가 되도록**
    당겨뒀다.
- `domain_name`·`hosted_zone_id` 가 **필수값으로 바뀌었다.** 한동안 선택값이었고
  이유도 적혀 있었다("쓰지도 않는 값을 필수로 두면 네트워크만 올리려는 사람이
  도메인부터 사야 한다"). 쓰는 자원이 생겼으므로 되돌렸다 — `alarm_email` 이
  `observability.tf` 가 생길 때 밟은 길과 같다. 이력은 지우지 않고
  description 에 남겼다.
- 검증: 테스트 43건(dns 16 · alb 27)을 더하고 **돌연변이 12건**을 넣어 전부
  탐지되는 것을 확인했다 — 80 포트를 forward 로 / 낡은 TLS 정책 / 인증서를
  validation 대신 인증서 자원에서 / matcher 를 `200-499` 로 / `/metrics` 를
  라우팅에 추가 / 태스크 보안그룹을 ALB 에 추가 / `deregistration_delay` 를
  기본값으로 / 이메일 검증으로 전환 / 호스팅 존을 또 만들기 / 아펙스 alias 의
  zone_id 를 호스팅 존 ID 로 / ALB 가 ipv4 인데 AAAA 추가 / 검증 대기 자원 제거.
  매번 원본을 바이트 단위로 되돌려 해시가 일치하는 것도 확인했다.
- 소요: 약 90분

## 2026-11-24 — ecs.tf 를 쓰면서 ADR-020 이 안 정해둔 것 둘을 정했다

- 맥락: ADR-020 이 "프론트는 별도 ECS 서비스(결정 6=S), Prometheus·Grafana 는
  백엔드 태스크의 사이드카(결정 5=R)"까지 정해뒀다. 코드를 쓰려고 보니 **둘 다
  '어떻게'가 비어 있었다.** dns.tf·alb.tf 때와 같은 모양이라 같이 적어둔다.
- ① **프론트 SSR 이 백엔드를 ALB 를 거쳐 부른다.** 로컬 compose 는
  `SERVER_API_URL: http://backend:8000` 으로 컨테이너 이름을 쓰는데 Fargate 에는
  그 이름이 없다. 후보가 셋이었다.
  - A: 공인 ALB 주소(`https://<도메인>`) — 코드 변경 0, 새 구성요소 0.
       대신 SSR 요청이 공인 ALB 를 한 번 왕복한다(헤어핀).
  - B: ECS Service Connect — 왕복이 VPC 안에서 끝나지만 태스크마다 Envoy
       프록시(~64MB)가 붙고 네임스페이스·포트 매핑 설정이 늘어난다.
  - C: 프론트를 백엔드 태스크에 합친다 — `localhost:8000` 이 되고 공인 IP
       1개 + 태스크 1개(월 ~$12)를 아끼지만 **ADR-020 결정 6 을 뒤집는 것**이고
       배포·스케일이 묶인다.
  - 고른 것: **A**. 근거: "ALB 경유 (추천)" 선택.
  - 되돌리는 조건: SSR 지연이 **측정 가능하게** 문제가 되거나 ALB LCU 비용이
    눈에 보이게 늘면 B 로 간다. 체감이 아니라 숫자로 판단한다.
- ② **관측 사이드카를 이번 커밋에서 뺐다.** ADR-020 결정 5 는 "`prometheus.yml`
  을 거의 그대로 쓴다"고 적었는데, **전달 수단이 없다** — 로컬은 설정 파일
  6개(`prometheus.yml`, `rules/`, `metrics_token`, Grafana provisioning 2개,
  dashboards)를 바인드 마운트로 받고 **Fargate 에는 바인드 마운트가 없다.**
  설정을 담은 이미지를 굽거나(ECR 리포 2개 추가) EFS 를 붙여야 하고, 그건 별도
  결정이다. 그래서 `/health` 200 까지만 먼저 간다.
  - 근거: "이번엔 빼고 /health 200 먼저 (추천)" 선택.
  - ALB 의 `/grafana` 타깃그룹은 **비어서 503 을 돌려준다.** 숨기지 않고 둔다.
  - ★ 테스트가 "반쯤 들어간 상태"를 막는다
    (`TestTheObservabilitySidecarsAreNotHalfAdded`) — 설정 없이 Grafana
    자격증명만 주입되면 사이드카는 뜨는데 빈 그래프를 보여주고, 그러면 **"지표가
    0" 과 "수집이 안 됨" 이 화면에서 같아 보인다.**
- ★ **cadvisor 는 Fargate 에서 불가능하다. 선택이 아니라 제약이다.**
  `privileged: true` 와 `/var/lib/docker`·`/sys`·`/` 바인드 마운트를 요구하는데
  Fargate 는 넷 다 제공하지 않는다. 운영 `prometheus.yml` 에서 `cadvisor` job 은
  **빠져야 한다** (`prometheus.yml` 의 주석은 "Docker Desktop 에서 node_exporter
  가 VM 수치를 보여준다"는 **로컬** 판단이었고, 운영에는 그대로 옮겨지지 않는다).
  컨테이너 단위 수치가 필요하면 ECS 태스크 메타데이터 엔드포인트로 다시 설계한다.
- ★ **README 의 사실 오류를 하나 고쳤다.** 5단계 설명에 "그 사이 태스크는 뜨지만
  헬스체크가 실패한다 — `champs_app` 이 아직 없으니 당연하다"라고 적혀 있었다.
  틀렸다. `/health` 는 `app/api/v1/health.py` 의 순수 liveness 체크로 **DB 를
  건드리지 않는다.** 빈 DB 로도 200 이 나온다. 이게 중요한 이유는 둘이다.
  1. 태스크가 헬스체크 실패로 계속 교체되면 ECS Exec 로 들어가 `roles.sql` 을
     돌릴 틈이 없다. 실제로는 안정돼 있어서 **계정을 만들 창이 열린다.**
  2. 반대로 `/health` 200 은 "DB 가 붙었다"를 뜻하지 않는다. 수락 기준으로
     쓸 때 그 차이를 알고 써야 한다. readiness 체크는 아직 없다.
  - 배운 것: **"당연하다"고 쓴 문장은 확인하지 않은 문장이다.** 이번에도 코드를
    읽기 전까지는 그럴듯했다.
- 그 밖에 정한 것과 이유:
  - **컨테이너 정의를 `locals` 로 뺐다.** `container_definitions =
    jsonencode({...})` 를 리소스 안에 직접 쓰면 python-hcl2 가 전체를 **문자열
    하나**로 돌려줘서 "시크릿이 평문 environment 에 섞였는가"를 테스트가 **볼 수
    없다.** `iam.tf` 가 정책을 `aws_iam_policy_document` data 소스로 쓴 것과 같은
    판단이다 — 테스트가 읽을 수 있는 형태로 쓰는 것이 조건이다.
    같은 이유로 `log_streams` 도 `toset([...])` 이 아니라 평범한 리스트로 두고
    변환은 `for_each` 쪽에서 한다.
  - **앱 컨테이너에 `MIGRATION_DATABASE_URL` 을 주지 않는다.**
    `docker-compose.prod.yml` 은 backend 서비스에 둘 다 주는데(로컬 검증의 편의)
    운영은 따라가지 않는다. `config.py` 가 직접 적어뒀다 — "런타임 코드가 이 값을
    읽으면 분리의 의미가 없어진다". 주입해도 **아무 증상이 없어서** 테스트로
    막는다.
  - **`image_tag` 를 기본값 없는 필수 변수로 뒀다.** `latest` 를 받아주면 무엇이
    떠 있는지 git 에서 되짚을 수 없다. `-dirty` 접미사도 validation 이 거부한다.
  - **`TRUST_PROXY_HEADERS = "true"`** — 로컬 검증에서는 꺼야 하고 ALB 뒤에서는
    켜야 한다. 끄면 속도 제한이 **동작하면서 틀린다**: 모든 요청이 ALB 의 IP
    하나로 보여 전체 사용자가 한 덩어리로 제한된다. 부하가 적으면 아무도 모른다.
  - **컨테이너 수준 healthCheck 를 두지 않았다.** ALB 타깃그룹이 이미 트래픽을
    막고 교체를 유발한다. 더하면 같은 일을 두 번 하면서 **실패 경로가 하나
    늘어난다** — 체크 명령이 쓰는 바이너리가 이미지에 없으면 서비스는 멀쩡한데
    태스크가 계속 교체된다. 되돌리는 조건: ALB 뒤에 없으면서 상시로 떠 있어야
    하는 태스크가 생기면 그 태스크에는 둔다.
  - **`deployment_circuit_breaker` + `rollback`** — 없으면 ECS 가 깨진 태스크를
    무한히 재시도하고 그 상태가 조용하다. 첫 배포에는 되돌릴 이전 버전이 없어
    "실패"로 끝나는데, 그래도 무한 재시도보다 낫다. 실패는 사람이 본다.
  - **마이그레이션과 시드를 한 태스크 정의로 묶었다.** 이미지·역할·로그·시크릿이
    전부 같고 다른 것은 명령 한 줄뿐이다. `run-task --overrides` 로 바꾼다.
    나누면 같은 것을 두 군데서 고쳐야 하고, 한쪽만 고쳐질 때 조용히 갈라진다
    (`Dockerfile` 의 `dbclient` 스테이지를 공유한 것과 같은 판단).
- 검증: `terraform fmt -check` · `validate` 통과. `ruff check` · `ruff format
  --check` · `mypy app/domain` 통과. `pytest` **934 passed, 33 deselected**
  (859 → +75, 전부 `test_terraform_ecs.py`).
  ★ **돌연변이 21건을 넣어 전부 탐지되는 것을 확인했다**: 클러스터 이름을
  리터럴로 / 패밀리 접두사 제거 / `METRICS_TOKEN` 을 평문 환경변수로 / 앱에 DDL
  자격증명 주입 / 시크릿의 JSON 키 선택 접미사 제거 / `rediss://` → `redis://` /
  `TRUST_PROXY_HEADERS` 끄기 / CORS 에 localhost 되살리기 / 로그 보존 기간 제거 /
  로그 그룹 접두사를 IAM 패턴 밖으로 / `awslogs-create-group` 켜기 / 공인 IP 끄기 /
  ARM → x86 / ECS Exec 끄기 / 태스크에 ALB 보안그룹 붙이기 / 서킷 브레이커 롤백
  끄기 / 헬스체크 유예 10초 / 컨테이너 포트 불일치 / 이미지 태그 `latest` /
  사이드카 자격증명만 반쯤 추가 / Fargate → EC2.
  매번 원본을 바이트 단위로 되돌려 해시가 일치하는 것도 확인했다.
- 소요: 약 100분

## 2026-11-24 — 프론트 브라우저 번들에 `localhost:8000` 이 박혀 있다 (아직 안 고쳤다)

- 증상: 운영에서 프론트 페이지는 정상 렌더되는데, 클라이언트 컴포넌트
  `components/health-status.tsx` 가 브라우저에서 부르는 `/health` 요청이
  `http://localhost:8000/health` 로 나간다. 사용자의 PC 를 가리키므로 실패한다.
- 기대 동작: 브라우저가 `https://<도메인>/health` 를 불러 200 을 받아야 한다.
  ALB 가 `/health` 를 백엔드로 보내도록 이미 라우팅돼 있다(`alb.tf`).
- 재현 절차:
  ```bash
  # 1. 빌드 시점에 변수가 주어지지 않는 것을 확인한다
  grep -n "NEXT_PUBLIC_API_URL" frontend/Dockerfile   # 출력 없음 = 주어지지 않는다

  # 2. 그 변수가 모듈 최상단에서 읽히는 것을 확인한다
  sed -n '33p' frontend/src/lib/api/config.ts

  # 3. 미설정 시 기본값을 확인한다
  grep -n "DEFAULT_API_URL" frontend/src/lib/api/config.ts
  ```
- 원인: Next.js 는 `NEXT_PUBLIC_*` 를 **빌드 시점에 번들에 인라인한다.**
  `frontend/Dockerfile` 의 builder 스테이지가 `npm run build` 를 돌릴 때 그 변수가
  없으므로 `process.env.NEXT_PUBLIC_API_URL` 자리가 `undefined` 로 치환되고,
  `config.ts` 의 `fromEnv` 가 기본값 `http://localhost:8000` 을 쓴다.
  **ECS 태스크 정의에 런타임 환경변수로 넣어도 브라우저 코드는 그 값을 보지
  못한다.** 서버 쪽(`SERVER_API_URL`)은 런타임에 읽으므로 영향이 없다 — 그래서
  SSR 페이지는 정상이고 **그 위젯만** 깨진다.
- 해결: **아직 하지 않았다.** `ecs.tf` 에 쓸모없는 환경변수를 넣어 "설정했다"고
  착각하게 만들지 않고, 주석으로 부재 이유를 적어뒀다. 후보가 둘이다.
  - A: `frontend/Dockerfile` 에 `ARG NEXT_PUBLIC_API_URL` 을 추가하고
    `push-images.ps1` 이 넘긴다 → **이미지가 도메인에 묶인다.** 도메인을 바꾸면
    재빌드다.
  - B: 같은 오리진이므로 브라우저에서는 상대 경로(`/health`)를 쓰게
    `config.ts` 를 고친다 → 이미지가 환경 중립으로 남는다. 대신 로컬 개발은
    프론트 3000 / 백엔드 8000 으로 오리진이 달라서 분기가 필요하다.
  - 지금 고르지 않은 이유: 둘 다 **프론트 이미지 재빌드**가 필요하고, 이 발견
    시점에 이미 빌드가 돌고 있었다. 그리고 A 는 "이미지가 환경에 묶인다"는
    트레이드오프가 있어 ADR 급이다.
- 배운 것: **"환경변수로 주입하면 된다"가 빌드 타임 치환 앞에서는 성립하지
  않는다.** 그리고 이 결함은 `/health` 200 수락 기준을 **통과하면서** 남아 있다 —
  사람이 브라우저로 열어보기 전까지 아무 테스트도 잡지 못한다. 프론트 단위
  테스트가 없다는 `docs/TEST_STRATEGY.md` 6장의 한계가 그대로 드러난 사례다.
- 소요: 약 15분 (발견과 범위 확인까지. 수정은 미룸)

## 2026-11-24 — terraform 이 자격증명을 못 찾았다 (EC2 메타데이터를 찾아갔다)

- 증상: 전날 멀쩡히 돌던 `terraform plan` 이 자원을 읽지도 못하고 죽는다.
  ```
  Error: No valid credential sources found
  Please see .../language/backend/s3 for more information about providing credentials.
  Error: failed to refresh cached credentials, no EC2 IMDS role found,
  operation error ec2imds: GetMetadata, ... Get
  "http://169.254.169.254/latest/meta-data/iam/security-credentials/":
  dial tcp 169.254.169.254:80: ... unreachable network.
  ```
- 기대 동작: SSO 프로필로 S3 백엔드의 state 를 읽고 `plan` 을 내놓아야 한다.
- 재현 절차:
  ```powershell
  # 새 PowerShell 창을 연다 (= AWS_PROFILE 이 없는 상태)
  cd C:\project\pokehelper\infra\terraform
  terraform plan
  ```
- 원인: **두 가지가 동시에 사라져 있었다.** 하나만 봤으면 다른 하나로 또 막혔다.
  1. **SSO 액세스 토큰이 만료됐다.** `~/.aws/sso/cache/*.json` 의 `expiresAt` 가
     `2026-11-24 01:54`, 확인 시각이 `10:14` 였다 — **8시간 세션**이다.
  2. **`AWS_PROFILE` 이 안 잡혀 있었다.** 새 터미널을 열었기 때문이다. 이 PC 에는
     `default` 프로필도 `~/.aws/credentials` 파일도 없고 `[profile pokehelper]`
     하나뿐이라, 환경변수로 지목하지 않으면 **쓸 프로필이 아예 없다.**
  ★ 그래서 자격증명 체인이 맨 마지막 후보인 **EC2 인스턴스 메타데이터**까지
  떨어졌다. `169.254.169.254` 가 그 주소다. 메시지가 네트워크 오류처럼 읽히는데
  **AWS 문제도 네트워크 문제도 아니다** — 노트북은 EC2 가 아니니 닿지 않는 것이
  정상이다. 이 메시지가 원인을 가리키지 않는 것이 이 건의 전부다.
- 해결: 둘을 순서대로 되살린다.
  ```powershell
  aws sso login --profile pokehelper
  $env:AWS_PROFILE = "pokehelper"
  aws sts get-caller-identity
  ```
  영구히 박으려면 `setx AWS_PROFILE pokehelper` (새 터미널에만 적용).
- ★ **내 진단이 처음에 틀렸다.** "README 에 프로필 설정이 아무 데도 적혀 있지
  않다"고 보고했는데, **§1 끝에 이미 있었다** (`export AWS_PROFILE=pokehelper`,
  PowerShell 주석까지). 문서를 안 읽고 "문서가 없다"고 단정한 것이다.
  실제로 빠져 있던 것은 **세 가지뿐**이고, 그래서 고친 것도 그 셋이다.
  1. 토큰이 **8시간**이라는 것
  2. `export`/`$env:` 가 **그 셸에서만** 유효하다는 것 — 명령이 "최초 1회"
     블록처럼 놓여 있어서 새 터미널에서 조용히 사라지는 것이 안 보였다
  3. ★ **증상 → 원인 대응표.** 비싼 것은 "환경변수를 설정하라"가 아니라
     "`169.254.169.254` 가 보이면 그건 프로필 문제다"를 아는 것이었다
- 배운 것: **"문서에 없다"는 진단은 문서를 읽은 뒤에만 해야 한다.** 그리고 문서에
  명령이 적혀 있는 것과 **그 명령의 수명이 적혀 있는 것**은 다르다. 수명이 없으면
  읽는 사람은 "한 번 하면 되는 것"으로 읽는다 — 어제의 나와 오늘의 나가 그랬다.
- `profile` 을 `backend.hcl` 과 프로바이더에 박는 쪽은 **일부러 안 했다.** 전날
  "코드로 옮기겠다"고 말했지만, 실제 실패는 **백엔드가 먼저** 난 것이라
  프로바이더만 고쳐서는 막히지 않는다. `backend.hcl` 을 고치면
  `terraform init -reconfigure` 가 필요하고, 환경변수 한 줄이 백엔드와 프로바이더
  **양쪽을 한 번에** 덮으므로 더 싸다. 되돌리는 조건: 프로필을 여러 개 쓰게 되면
  그때 옮긴다 (`backend.hcl.example` 에 주석으로 남겨뒀다).
- 소요: 약 20분 (진단 10분, 문서화 10분)

## 2026-11-24 — 시크릿 주입 1단계가 실패했는데 4단계가 성공했다

- 증상: README 5-C 의 첫 명령이 실패한다.
  ```
  Error: Output "app_secret_template" not found
  ```
  ★ 그런데 **그 뒤 명령이 전부 성공한다.** `put-secret-value` 가 정상적으로
  `VersionId` 를 돌려주고, 겉보기에 시크릿 주입이 끝난 것처럼 보인다.
- 기대 동작: 1번이 키 6개를 담은 빈 JSON 템플릿을 만들고, 사람이 값을 채운 뒤
  4번이 그 내용을 올려야 한다. 1번이 실패하면 **그 자리에서 멈춰야** 한다.
- 재현 절차:
  ```powershell
  cd C:\project\pokehelper\infra\terraform
  # -target 으로만 apply 한 state 에서
  terraform output -json app_secret_template      # Error: Output not found
  terraform output -raw app_secret_name           # ★ 이건 된다
  ```
- 원인: **`-target` 은 타깃 자원에 의존하지 않는 출력을 평가하지 않는다.**

  | 출력 | 참조하는 것 | state 에 있나 |
  |---|---|---|
  | `app_secret_name`·`app_secret_arn` | `aws_secretsmanager_secret.app` (자원) | 있다 |
  | `app_secret_template`·`app_secret_keys_documented` | `local.app_secret_keys` 뿐 | ★ **없다** |

  그래서 `$json` 이 빈 값이 되고 → `WriteAllText` 가 **빈 파일**을 만들고 →
  notepad 에 빈 파일이 열리고 → `put-secret-value` 가 그 빈 내용을 올린다.
  ★ **Secrets Manager 는 내용이 JSON 인지 검사하지 않는다.** 그래서 실패가
  조용히 성공으로 포장된다.

  ★ 이 메커니즘은 **하루 전에 이미 겪었다.** `dns.tf` 의 `service_url` 출력이
  같은 이유로 안 나왔고, 그 자리에서 "`-target` 은 타깃에 의존하지 않는 출력을
  평가하지 않는다"고 설명까지 적었다. 그런데 다음 날 5-C 절차를 그대로 안내할
  때 **그 절차의 첫 줄이 바로 그런 출력에 의존한다는 것을 확인하지 않았다.**
- 해결: 둘 다 고쳤다.
  1. **대체 절차** — 키 이름을 `secrets.tf` 의 `app_secret_keys` 블록에서 읽어
     템플릿을 만든다. "키를 손으로 타이핑하지 않는다"는 원래 의도는 유지된다.
     `읽은 키 N 개` 를 찍어서 6이 아니면 멈추게 했다.
  2. ★ **올린 뒤 검증 단계를 추가했다.** `put-secret-value` 의 성공은
     "올라갔다"만 뜻하고 내용이 맞다는 뜻이 아니다. 이 단계가 아예 없었다.
- ★ **검증을 값을 보지 않고 한다.** `get-secret-value` 로 받아 키 이름과
  **길이만** 출력하면, 길이 산술로 DSN 구조가 맞는지 확인된다.
  호스트명 64자 / 비밀번호 32자 / DB `champs` 일 때 141·145·150 이 나와야 한다.
  한 글자라도 다르면 `+asyncpg` 누락, 포트 중복, `sslmode` 오타 중 하나다.
  실제로 이번 건은 세 값이 **바이트 단위로 정확히** 141·145·150 이었고, 그래서
  "사람이 notepad 에서 직접 채웠다"가 값을 보지 않고 확정됐다.
- 배운 것 둘.
  - **같은 메커니즘에 두 번 걸렸다.** 메커니즘을 아는 것과 **그 메커니즘이
    적용되는 자리를 점검하는 것**은 다른 일이다. 전날 적어둔 설명이 다음 날의
    절차 점검으로 이어지지 않았다.
  - ★ **"성공했다"는 종료 코드를 믿으면 안 되는 구간이 있다.** 저장소가 내용을
    검사하지 않는 API(`put-secret-value`)는 쓰레기도 받아준다. 그런 호출 뒤에는
    **읽어서 확인하는 단계가 절차의 일부**여야 한다.
    `tests/integration/test_terraform_secrets.py` 는 "값이 코드·plan·state 에
    들어오는 경로"를 막지만, **올라간 값이 맞는지는 검사 범위 밖이다** —
    실제 계정을 보지 않기 때문이다 (`terraform_support.py` 머리말의 한계).
- 소요: 약 25분

## 2026-11-24 — 비밀번호를 화면에 출력하는 절차를 만들었고, 그게 노출로 이어졌다

- 증상: 운영 DB 비밀번호 4개(RDS 마스터 1 + 우리 계정 3)가 외부 대화에 평문으로
  남았다. 터미널 출력을 그대로 붙여넣는 과정에서 같이 들어갔다.
- 기대 동작: 비밀번호는 **사람 눈에도 띄우지 않고** 로컬에서 컨테이너로 건너가야
  한다. 저장소에도, 화면에도, 명령 기록에도 남지 않아야 한다.
- 재현 절차:
  ```powershell
  cd C:\project\pokehelper\infra\terraform
  $sec = aws secretsmanager get-secret-value --secret-id (terraform output -raw app_secret_name) `
    --query SecretString --output text --profile pokehelper | Out-String | ConvertFrom-Json
  function Get-Pw($dsn) { [regex]::Match($dsn, '://[^:/]+:([^@]+)@').Groups[1].Value }
  "app : $(Get-Pw $sec.DATABASE_URL)"      # ★ 여기서 평문이 화면에 찍힌다
  ```
- 원인: **절차 설계의 결함이고, 그 절차는 내가 만들었다.** 컨테이너 안쪽은
  `read -s` 로 가렸는데 **로컬 출력은 가리지 않았다.** 가린 쪽만 보고 "비밀번호를
  노출하지 않는 절차"라고 불렀다. 실제로는 **컨테이너에 붙여넣기 위해 사람이
  읽어야 하는 구조**였고, 읽을 수 있으면 복사되고 복사되면 어딘가에 붙는다.
- 해결: 두 단계로 처리했다.
  1. **교체.** 앱 비밀번호 3개는 `roles.sql` 을 아직 돌리기 **전**이라 DB 에
     존재하지 않았다 — 시크릿 값만 바꾸면 끝이었다(가장 싼 시점). 그 뒤에
     `roles.sql` 이 새 값으로 계정을 만들었다. RDS 마스터는 이미 살아 있어서
     `aws rds modify-db-instance --rotate-master-user-password --apply-immediately`
     로 AWS 가 교체했다(새 비밀번호는 항상 28자).
  2. **절차 교체.** 화면 출력 대신 **`Set-Clipboard`** 로 넘긴다. 사람은 값을
     보지 않고 붙여넣기만 한다. `infra/terraform/README.md` 5-D 에 반영했다.
- ★ **실제 침해 위험은 낮았다. 그래도 바꿨다.** RDS 는 프라이빗 서브넷 +
  `publicly_accessible = false` 이고 보안그룹이 태스크 SG 에서 오는 5432 만 받는다.
  인터넷에서는 비밀번호를 알아도 접속 경로가 없다. 바꾼 이유는 **이 프로젝트가
  다층 방어를 전제로 설계됐기 때문**이다 — "네트워크가 막으니 비밀번호는 덜
  중요하다"로 가면 ADR-015·ADR-022 가 한 일이 무의미해진다. 한 층이 무너진 걸
  알면서 두는 것은 층을 하나 지운 것과 같다.
- 배운 것 둘.
  - ★ **"가렸다"는 가린 구간에 대해서만 참이다.** `read -s` 는 입력을 가리고
    로컬 출력은 가리지 않는다. 비밀이 지나가는 **전 구간**을 보지 않으면
    "안전한 절차"라는 말이 절반만 맞는다.
  - **사람이 읽어야 하는 비밀은 이미 노출된 것으로 취급해야 한다.** 클립보드로
    바꾼 것이 본질적 개선인 이유는 "사람이 읽을 필요를 없앤 것"이다.
- ★ **남은 한계**: 클립보드 방식도 완전하지 않다. 클립보드 히스토리를 켜둔
  환경에서는 값이 남는다. 5-D 끝에 `Set-Clipboard -Value " "` 를 넣었지만 그건
  현재 값만 지운다. 근본 해법은 **사람을 경로에서 빼는 것** — 태스크 역할에
  시크릿 읽기 권한을 주고 컨테이너가 직접 꺼내게 하거나, RDS IAM 인증으로
  비밀번호 자체를 없애는 쪽이다. 지금은 그 권한을 **일부러 주지 않았으므로**
  (앱 컨테이너가 DB 마스터를 읽을 수 있는 상태를 막으려고) 트레이드오프가 있다.
- 소요: 약 30분 (교체 10분, 절차 수정·기록 20분)

## 2026-11-24 — 시크릿을 바꿨는데 도는 태스크는 옛 값을 쓰고 있었다

- 증상: `roles.sql` 로 DB 계정을 만들고 마이그레이션·시드까지 전부 성공했는데
  API 만 **500** 이다. `/health` 는 200 이고 `/`·`/metrics`·`/grafana/` 도 전부
  기대대로다. 백엔드 로그:
  ```
  asyncpg.exceptions.InvalidPasswordError: password authentication failed for user "champs_app"
  ```
- 기대 동작: `GET /api/v1/meta/usage` 가 DB 를 조회하고, 수집 데이터가 없으므로
  `NotAvailableError` → **404** 를 돌려줘야 한다.
- 재현 절차:
  ```powershell
  curl.exe -s -o NUL -w "%{http_code}`n" https://paoriprofessor.click/api/v1/meta/usage   # 500
  aws logs tail /ecs/pokehelper-prod/backend --since 20m --profile pokehelper
  ```
  컨테이너 안에서 둘을 갈라보면 원인이 바로 좁혀진다:
  ```sh
  python -c "
  import asyncio
  from redis.asyncio import Redis
  from app.core.config import get_settings
  async def main():
      r = Redis.from_url(get_settings().redis_url, decode_responses=True)
      print('PING ->', await r.ping())
  asyncio.run(main())
  "
  ```
  Redis 는 `True`, DB 만 `InvalidPasswordError` 였다.
- 원인: ★ **ECS 는 시크릿을 태스크가 뜰 때 한 번만 읽는다.** 순서가 이랬다.
  1. `ecs.tf` apply → 백엔드 태스크가 **그때의** `DATABASE_URL` 을 받아 기동
  2. 비밀번호 노출 대응으로 **시크릿 교체**
  3. `roles.sql` 이 **새** 비밀번호로 DB 계정 생성
  4. 태스크만 **옛** 값을 들고 있음

  증거 세 개가 맞물렸다 — ① 컨테이너 안에서 시크릿의 현재 값으로는
  `psql -U champs_app` 이 **성공**했고(시크릿 == DB), ② 교체 **이후에** 띄운
  `migrate` 일회성 태스크는 정상이었고(run-task 는 띄울 때 읽는다),
  ③ `pg_stat_ssl` 에 `champs_app` 연결이 **아예 없었다**(연결 시도가 전부 실패).
- 해결:
  ```powershell
  aws ecs update-service --cluster pokehelper-prod --service pokehelper-prod-backend `
    --force-new-deployment --profile pokehelper
  ```
  이미지는 그대로이고 태스크만 새로 떠서 시크릿을 다시 읽는다. 무중단이다
  (`deployment_minimum_healthy_percent = 100`). 직후 `/api/v1/meta/usage` 가
  **404 + `{"detail":"'Singles' 포맷의 활성 스냅샷이 없다"}`** 로 바뀌었다.
- ★ **이건 내 안내의 공백이었다.** 교체를 지시할 때 `METRICS_TOKEN` 에 대해서는
  "재배포해야 반영된다"고 적어놓고 **`DATABASE_URL` 에는 같은 규칙을 적용하지
  않았다.** 한 번 쓴 사실을 옆 칸에 옮기지 않았다.
- 배운 것: **"주입"은 한 번 일어나는 사건이지 연결이 아니다.** ECS 의
  `secrets`/`valueFrom` 은 시크릿을 *구독*하는 것처럼 보이지만 기동 시 복사일
  뿐이다. 그래서 **시크릿 교체 절차에는 "누가 그 값을 들고 있는가"가 반드시
  포함돼야 한다** — 서비스는 재배포, 일회성 태스크는 해당 없음, 열려 있는 ECS
  Exec 세션은 옛 태스크와 함께 조용히 끊긴다.
- `README.md` 5-C 에 `--force-new-deployment` 절을 추가했고, ADR-022 의 `## 결과`
  에 "본문이 값을 **바꿀** 때 무엇을 더 해야 하는지 적지 않았다"로 기록했다.
- 소요: 약 25분 (진단 10분 — 로그 한 번으로 확정됐다)

## 2026-11-24 — 배포 당일에 닫힌 미지 둘과, 예측이 빗나간 셋

문제 해결이 아니라 **열어뒀던 질문의 종결 기록**이다.

- ★ **asyncpg 가 TLS 를 쓰는가 — 쓴다.** 11-23 에 "SQLAlchemy 공식 문서가 asyncpg
  의 SSL 파라미터를 전혀 설명하지 않는다. asyncpg 기본값이 `prefer` 이므로
  `?sslmode=require` 를 붙이지 말자. 다만 SQLAlchemy 가 `ssl=None` 을 명시적으로
  넘기는지는 확인 못 했다"로 남겨뒀다. 컨테이너 안에서 **앱과 같은 경로**로
  물었다:
  ```sh
  python -c "
  import asyncio
  from sqlalchemy import text
  from app.db.session import get_engine
  async def main():
      async with get_engine().connect() as c:
          r = await c.execute(text('select current_user, ssl, version from pg_stat_ssl join pg_stat_activity using (pid) where pid = pg_backend_pid()'))
          print('asyncpg ->', r.first())
  asyncio.run(main())
  "
  ```
  → `asyncpg -> ('champs_app', True, 'TLSv1.3')`. **코드 변경 없이 암호화된다.**
  ★ `psql` 로 보는 것으로는 답이 안 됐다 — psql 은 자기 연결만 보여주고, 궁금한
  것은 **앱이 쓰는 드라이버**였다. 측정 도구를 대상에 맞춘 것이 요점이다.
  (`rds.force_ssl` 은 `SHOW` 가 `unrecognized configuration parameter` 로 떨어져
  확인하지 못했다. 이유는 추측하지 않는다 — 그리고 알 필요가 없어졌다.)
- ★ **arm64 이미지가 Graviton 에서 도는가 — 돈다.** ADR-020 의 11-23 결과가
  *"진짜 Graviton CPU 위에서 도는지는 ECS 가 띄울 때 처음 드러난다"* 로 끝나
  있었다. 두 서비스 모두 `running: 1` 로 떴고 `exec format error` 는 없었다.
- 예측이 빗나간 셋 (전부 **덜 나쁜 쪽**으로):
  | 내가 말한 것 | 실제 | 왜 틀렸나 |
  |---|---|---|
  | 인증서 검증에 "몇 분 멈춘다" | **0초** | NS 일치를 미리 확인해둬서 CNAME 이 올라간 32초 사이에 ACM 이 끝냈다 |
  | `pg_read_all_data` 는 **대체 경로**를 탈 것 | 그냥 **성공** | RDS 마스터는 슈퍼유저가 아니지만 `rds_superuser` 멤버라 ADMIN 옵션이 있었다. `roles.sql` 주석이 두 경우를 다 적어둔 것이 맞았다 |
  | `/api/v1/meta/usage` 는 **404** 일 것 | **500** | 위 항목의 시크릿 건. 예측 자체는 설계상 맞았고 **상태가 틀렸다** |
- ★ 그리고 **`plan` 과 `apply` 숫자가 어긋난 것을 한 번 그냥 넘겼다.**
  `Plan: 8 to add` → `7 added` 를 "태스크 정의가 즉시 완료돼서"라고 설명했는데
  틀렸다. 실제로는 `migrate` 로그 그룹이 `-target` 그래프 밖이라 만들어지지
  않았다. 다음 apply 가 `2 to add` 로 나와서야 드러났다.
  **숫자가 다르면 설명이 떠오르더라도 세어 봐야 한다.**
- 소요: — (배포 전체에 걸쳐)

## 2026-11-25 — Airflow DAG 를 EventBridge 로 옮기면서 못 옮긴 것 둘을 정했다

- 맥락: ADR-020 결정 4 가 "EventBridge Scheduler → ECS RunTask" 로 정했고,
  `iam.tf` 의 주석은 *"스케줄러가 돌릴 태스크 정의. 수집과 만료 정리 **둘이다**"*
  로 **스케줄 2개**를 가정하고 있었다. 그런데 원본인 Airflow DAG 를 읽어보니
  **EventBridge 로 옮길 수 없는 속성이 셋** 있었다. 추측해서 넘기면 매일 06:00 에
  조용히 틀린 일이 일어나는 자리라 전부 정하고 기록한다.
- ① **작업 간 의존** (`ingest >> cleanup`)
  - DAG 주석: *"수집이 성공해야 정리를 돌린다. 반대로 하면, 수집이 실패한 날
    오래된 스냅샷만 지워져 보존 기간이 짧아진다."*
  - EventBridge Scheduler 에는 **작업 간 의존 개념이 없다.** 후보가 셋이었다.
    - A: **한 태스크 안에서 `&&`** — 추가 자원 0, 의존이 정확히 보존된다
    - B: 스케줄 2개 (ADR-020 이 가정한 모양) — 각 작업 상태가 따로 보이지만
      **의존이 사라진다**
    - C: Step Functions — 완전히 표현 가능하지만 ADR-020 의 범위를 넘는다
  - 고른 것: **A**. 근거: "한 태스크에서 && 로 (추천)" 선택.
  - ★ 종료 코드 **2**(적재는 됐으나 한영 매핑 해석 실패)도 정리를 막는다.
    그것도 DAG 와 같다 — Airflow 에서 2 는 실패고 하류가 돌지 않았다.
  - 포기한 것: 두 작업의 상태가 **한 종료 코드로 합쳐진다.** "수집은 됐는데
    정리가 실패"를 종료 코드만으로는 구분 못 한다(로그에는 남는다).
- ② **종료 코드 기반 재시도** (`retries: 2, retry_delay: 10분`)
  - ★ EventBridge 의 `retry_policy` 는 **RunTask API 호출이 실패했을 때**만
    재시도한다. 태스크가 정상적으로 떠서 종료 코드 1·2 로 죽는 경우는 **재시도
    대상이 아니다.** 즉 거기에 `2` 를 적어도 DAG 동작이 재현되지 않는다.
  - 고른 것: **재시도 없음**, 그리고 `maximum_retry_attempts = 0` 을 **명시**한다.
    근거: "재시도 없이 간다 (추천)" 선택.
  - ★ **블록을 지우지 않고 0 을 적은 이유**: 없으면 "정했다"와 "안 봤다"를
    구분할 수 없다. 숫자를 올려두는 쪽이 더 나쁘다 — **재현된다고 착각**하게 된다.
  - 되돌리는 조건: "하루를 건너뛰는 것"이 실제로 문제가 되면 Step Functions 로
    옮긴다. 거기서는 1 만 재시도하고 2 는 알림으로 보낼 수 있다 — DAG 주석이
    *"지금은 구분하지 않는다"* 고 적어둔 그 구분이다.
- ③ **시간대** — ★ 이게 가장 조용한 함정이었다
  - DAG 의 `schedule="0 6 * * *"` 은 Airflow 기본값이라 **UTC** 로 해석된다.
    즉 실제로는 **한국 시간 오후 3시**에 돌고 있었다. 주석에는 "매일 06:00"
    이라고만 적혀 있어 **어느 쪽을 의도했는지 알 수 없었고, 틀렸는지도 알 수
    없었다.**
  - 고른 것: **06:00 Asia/Seoul** 로 `schedule_expression_timezone` 을 명시.
    근거: "06:00 KST 로 명시 (추천)" 선택.
  - `data.tf` 가 RDS 백업 창을 03:00-04:00 KST 로 고른 기준("한국 사용자가 가장
    적은 시각")과 같은 기준이다. 백업 창·유지보수 창(월 04:30 KST)과도 겹치지
    않는다. **기존 동작(15:00 KST)과 달라지므로 바꾼 것으로 기록한다.**
- ④ **`execution_timeout` 은 `timeout` 명령으로 옮겼다.** ECS RunTask 에는 실행
  시간 상한이 **없다.** 멈춘 태스크가 영원히 돌면서 과금되고, 알려주는 것은
  청구서뿐이다. DAG 의 15분/5분을 그대로 `timeout 900` / `timeout 300` 으로 썼다.
  초과 시 124 로 끝나므로 `&&` 가 거기서 멈춘다.
- ★ **`migrate` 를 재사용하지 않고 `ingest` 패밀리를 따로 뒀다.** 이미지·역할·
  시크릿이 같아서 `--overrides` 로도 돌지만(11-25 수동 검증은 그렇게 했다),
  상시 스케줄은 분리했다.
  1. ★ **로그가 섞이면 "어젯밤 수집이 돌았나"를 못 본다.** `migrate` 는 배포할
     때 사람이 돌리는 것이고 수집은 매일 무인으로 돈다.
  2. **기본 command 가 진짜 명령이어야 한다.** 스케줄에 `--overrides` 를 넣으면
     "이 태스크가 무엇을 하는가"가 두 파일로 나뉜다.
  3. 나중에 실패 경보를 붙일 **대상 로그 그룹이 분리돼 있어야** 한다.
  - ★ 대가: 컨테이너 정의를 **거의 같은 내용으로 두 번** 적는다. `merge()` 로
    묶지 않은 이유는 python-hcl2 가 함수 호출을 문자열 하나로 돌려줘 **테스트가
    안을 볼 수 없게** 되기 때문이다. 그래서 **중복을 없애는 대신 중복이 갈라지는
    것을 테스트가 집행한다**(`TestScheduledIngestMatchesMigrate` — 이미지·평문
    환경변수가 같은지, 그리고 수집 쪽에만 `MIGRATION_DATABASE_URL` 이 **없는지**).
- ★ **수집 태스크에 DDL 자격증명을 주지 않는다.** `run_ingest`·`cleanup_snapshots`
  는 둘 다 `DATABASE_URL`(= `champs_app`)로 붙고 연산이 INSERT·UPDATE·DELETE
  뿐이다. TRUNCATE 도 쓰지 않는다. 매일 무인으로 도는 태스크에 DDL 을 들려 보낼
  이유가 없다 (ADR-015).
- 검증: `terraform fmt -check` · `validate` 통과. `ruff check`/`format --check` ·
  `mypy app/domain` 통과. `pytest` **980 passed, 33 deselected** (934 → +46).
  ★ **돌연변이 22건 중 21건이 처음에 탐지됐고, 1건이 뚫렸다** — 아래 참조.
- 소요: 약 120분

## 2026-11-25 — 변수의 기본값이 자기 validation 을 어겨도 아무도 몰랐다

- 증상: 돌연변이 검증에서 **1건이 탐지되지 않았다.** `ingest_format` 의 기본값을
  `"Singles"` 에서 `"Single"`(s 빠짐)로 바꿨는데 테스트 전체가 통과했다.
- 기대 동작: 변수에 `validation { condition = contains(["Singles","Doubles"], ...) }`
  를 달아뒀으므로, 그 목록에 없는 기본값은 **커밋 전에 걸려야** 한다.
- 재현 절차:
  ```bash
  cd /c/project/pokehelper
  sed -i 's/  default     = "Singles"/  default     = "Single"/' infra/terraform/variables.tf
  docker compose exec -T backend uv run pytest tests/integration/ -q   # 통과해버린다
  sed -i 's/  default     = "Single"$/  default     = "Singles"/' infra/terraform/variables.tf
  ```
- 원인: 테스트가 **변수의 기본값과 그 변수의 validation 을 대조한 적이 없었다.**
  `validation` 블록이 있는지, 정규식이 무엇을 막는지는 보고 있었는데, **기본값이
  그 규칙을 통과하는지**는 아무도 안 봤다. 두 정보가 같은 파일 같은 블록 안에
  있는데도 서로를 검사하지 않았다.
- 해결: `TestDefaultsSatisfyTheirOwnValidation` 추가. `contains(...)` 를 쓰는
  **모든** 변수에 대해 기본값이 목록 안/밖에 맞게 있는지 본다.
  ★ 첫 구현이 **거짓 양성**을 냈다 — `db_master_username` 이 `!contains([...])`
  로 **금지 목록**을 쓰고 있었다. 허용 목록만 가정했던 것이다. 부정 여부를
  같이 뽑아 두 방향 다 검사하게 고쳤고, 덕분에 **검사 범위가 오히려 넓어졌다.**
  `lower(var.x)` 가 조건에 있으면 기본값도 소문자로 맞춰 비교한다.
- 배운 것 둘.
  - ★ **"검증 장치가 있다"와 "검증 장치가 올바른 값을 지키고 있다"는 다르다.**
    validation 은 **사람이 넘기는 값**을 검사하지 **자기 기본값**은 검사하지
    않는다. 아무 값도 안 넘기는 사람(= 대부분의 경우)은 그 규칙 밖에 있다.
  - ★ **돌연변이 1건이 뚫린 것이 테스트 1건보다 비싼 정보였다.** 21/22 를
    "거의 다 잡았다"로 넘겼으면 이 구멍은 남았다. 비공허성 검증은 통과율이 아니라
    **뚫린 항목**을 보는 것이다.
- 소요: 약 25분
