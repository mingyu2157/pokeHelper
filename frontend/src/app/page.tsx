import Link from "next/link";
import { HealthStatus } from "@/components/health-status";

export default function Home() {
  return (
    <main className="mx-auto flex min-h-screen max-w-2xl flex-col justify-center gap-8 px-6 py-16">
      <header className="space-y-2">
        <h1 className="text-3xl font-bold">pokeHelper</h1>
        <p className="text-slate-400">포켓몬 챔피언스 파티 구축 + 선출 판단 보조.</p>
      </header>

      <nav>
        <Link
          href="/meta"
          className="inline-block rounded border border-slate-700 px-4 py-2 text-sm hover:border-slate-500 hover:bg-slate-900"
        >
          메타 현황 보기 →
        </Link>
      </nav>

      <HealthStatus />

      <footer className="text-xs text-slate-500">
        본 서비스는 포켓몬 공식 제휴가 아닙니다.
      </footer>
    </main>
  );
}
