import type { components } from "@/types/api";
import { API_BASE_URL, SERVER_API_BASE_URL } from "./config";

/**
 * ★ 타입을 손으로 쓰지 않는다 (CLAUDE.md 6장) ★
 *
 * 백엔드 스키마에서 생성한 `src/types/api.ts` 를 참조한다. 손으로 다시 쓰면
 * 백엔드가 필드를 바꿔도 프론트는 **타입체크를 통과하면서** 틀린 모양을
 * 믿는다. 실패가 런타임까지 미뤄진다.
 *
 * 생성: `npm run generate:api` (스키마는 backend/openapi.json)
 */
export type UsageList = components["schemas"]["UsageListOut"];
export type UsageEntry = components["schemas"]["UsageEntryOut"];
export type PokemonMeta = components["schemas"]["PokemonMetaOut"];
export type Attribution = components["schemas"]["AttributionOut"];
export type OptionUsage = components["schemas"]["MetaOptionOut"];
export type SpreadUsage = components["schemas"]["MetaSpreadOut"];

export class MetaUnavailableError extends Error {}

/**
 * 서버 컴포넌트에서 호출한다.
 *
 * 브라우저가 쓰는 주소(localhost:8000)와 컨테이너 안에서 쓰는 주소
 * (backend:8000)가 다르다. SSR 은 컨테이너 안에서 도므로 localhost 로
 * 부르면 **자기 자신**을 부른다.
 */
async function getJson<T>(path: string): Promise<T> {
  const response = await fetch(`${SERVER_API_BASE_URL}${path}`, {
    // 메타는 하루 한 번 갱신된다. 그래도 캐시하지 않는다 — 수집 시각을
    // 화면에 띄우는데 캐시된 옛 시각을 보여주면 그 표시가 거짓이 된다.
    cache: "no-store",
  });

  if (response.status === 404) {
    throw new MetaUnavailableError("활성 스냅샷이 없다");
  }
  if (!response.ok) {
    throw new Error(`HTTP ${response.status}`);
  }
  return (await response.json()) as T;
}

export function fetchUsageList(limit = 30): Promise<UsageList> {
  return getJson<UsageList>(`/api/v1/meta/usage?limit=${limit}`);
}

export function fetchPokemonMeta(pokemonId: string): Promise<PokemonMeta> {
  return getJson<PokemonMeta>(`/api/v1/meta/pokemon/${encodeURIComponent(pokemonId)}`);
}

export { API_BASE_URL };
