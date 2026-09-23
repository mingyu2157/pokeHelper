import { HealthStatus } from "@/components/health-status";

export default function Home() {
  return (
    <main className="mx-auto flex min-h-screen max-w-2xl flex-col justify-center gap-8 px-6 py-16">
      <header className="space-y-2">
        <h1 className="text-3xl font-bold">pokeHelper</h1>
        <p className="text-slate-400">
          포켓몬 챔피언스 파티 구축 + 선출 판단 보조. Phase 0 뼈대입니다.
        </p>
      </header>

      <HealthStatus />

      <footer className="text-xs text-slate-500">
        본 서비스는 포켓몬 공식 제휴가 아닙니다.
      </footer>
    </main>
  );
}
