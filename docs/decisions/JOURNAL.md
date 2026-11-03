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
