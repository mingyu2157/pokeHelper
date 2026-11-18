/**
 * 백엔드 주소. 호출하는 쪽이 브라우저냐 서버냐에 따라 다르다.
 *
 * ★ 둘을 하나로 합치면 한쪽이 깨진다 ★
 *
 * - 브라우저는 컨테이너 네트워크를 모른다. `http://backend:8000` 은 못 찾는다.
 * - 서버 컴포넌트는 컨테이너 **안**에서 돈다. `http://localhost:8000` 은
 *   프론트 자기 자신을 가리킨다.
 *
 * 그래서 변수를 둘로 나누고, 서버용은 컨테이너 네트워크 이름을 기본값으로 둔다.
 */

const DEFAULT_API_URL = "http://localhost:8000";

/**
 * ★ `??` 만 쓰면 빈 문자열이 통과한다 ★
 *
 * `??` 는 null/undefined 에서만 기본값으로 넘어간다. compose 가 변수를
 * **빈 값으로** 넘기면(오타, 미설정 변수 치환) `""` 가 그대로 쓰여서
 * `fetch("/api/...")` 가 되고, 서버 컴포넌트에서는 상대 경로를 해석할 기준이
 * 없어 `ECONNREFUSED` 로 끝난다. 원인이 URL 이라는 게 드러나지 않는다.
 */
function fromEnv(...candidates: (string | undefined)[]): string {
  for (const candidate of candidates) {
    if (candidate && candidate.trim() !== "") {
      return candidate;
    }
  }
  return DEFAULT_API_URL;
}

/** 브라우저에서 쓰는 주소. `NEXT_PUBLIC_` 접두사가 있어야 번들에 들어간다. */
export const API_BASE_URL = fromEnv(process.env.NEXT_PUBLIC_API_URL);

/** 서버 컴포넌트·라우트 핸들러에서 쓰는 주소. 번들에 들어가지 않는다. */
export const SERVER_API_BASE_URL = fromEnv(
  process.env.SERVER_API_URL,
  process.env.NEXT_PUBLIC_API_URL,
);
