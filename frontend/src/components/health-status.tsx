"use client";

import { useEffect, useState } from "react";

import { API_BASE_URL, fetchHealth, type HealthResponse } from "@/lib/api/health";

type State =
  | { kind: "loading" }
  | { kind: "ok"; data: HealthResponse }
  | { kind: "error"; message: string };

export function HealthStatus() {
  const [state, setState] = useState<State>({ kind: "loading" });

  useEffect(() => {
    let alive = true;

    fetchHealth()
      .then((data) => {
        if (alive) setState({ kind: "ok", data });
      })
      .catch((error: unknown) => {
        if (alive) {
          setState({
            kind: "error",
            message: error instanceof Error ? error.message : "알 수 없는 오류",
          });
        }
      });

    return () => {
      alive = false;
    };
  }, []);

  return (
    <section className="rounded-lg border border-slate-800 bg-slate-900 p-5">
      <h2 className="mb-1 text-sm font-semibold text-slate-300">백엔드 연결 상태</h2>
      <p className="mb-4 font-mono text-xs text-slate-500">GET {API_BASE_URL}/health</p>

      {state.kind === "loading" && <p className="text-slate-400">확인 중…</p>}

      {state.kind === "ok" && (
        <pre className="overflow-x-auto rounded bg-slate-950 p-3 font-mono text-sm text-emerald-400">
          {JSON.stringify(state.data, null, 2)}
        </pre>
      )}

      {state.kind === "error" && (
        <p className="font-mono text-sm text-red-400">연결 실패: {state.message}</p>
      )}
    </section>
  );
}
