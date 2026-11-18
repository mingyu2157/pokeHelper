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
