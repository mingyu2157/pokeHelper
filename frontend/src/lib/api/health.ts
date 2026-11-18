import type { components } from "@/types/api";
import { API_BASE_URL } from "./config";

/**
 * ★ 손으로 쓰던 타입을 생성 타입으로 바꿨다 (CLAUDE.md 6장) ★
 *
 * 전에는 `{ status: "ok"; service: string; version: string }` 를 여기 직접
 * 적어뒀다. 백엔드가 필드를 바꿔도 이 파일은 모르고, 타입체크는 통과한다.
 * 이제 `backend/openapi.json` 에서 생성한 타입을 참조한다.
 */
export type HealthResponse = components["schemas"]["HealthResponse"];

export { API_BASE_URL };

/**
 * 브라우저에서 백엔드 /health 를 직접 호출한다.
 * 컨테이너 내부 주소(http://backend:8000)가 아니라 호스트에서 보이는 주소를 쓴다.
 */
export async function fetchHealth(): Promise<HealthResponse> {
  const response = await fetch(`${API_BASE_URL}/health`, { cache: "no-store" });

  if (!response.ok) {
    throw new Error(`HTTP ${response.status}`);
  }

  return (await response.json()) as HealthResponse;
}
