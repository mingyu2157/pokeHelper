export type HealthResponse = {
  status: "ok";
  service: string;
  version: string;
};

export const API_BASE_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

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
