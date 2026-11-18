import Link from "next/link";
import { Attributions } from "@/components/attribution";
import { UsageTable } from "@/components/usage-table";
import { MetaUnavailableError, fetchUsageList } from "@/lib/api/meta";

export const metadata = {
  title: "메타 현황 — pokeHelper",
  description: "포켓몬 챔피언스 사용 순위와 채용률",
};

// 수집 시각을 화면에 띄우므로 캐시하지 않는다. 캐시된 옛 시각을 보여주면
// 그 표시 자체가 거짓이 된다.
export const dynamic = "force-dynamic";

export default async function MetaPage() {
  let data;
  try {
    data = await fetchUsageList(30);
  } catch (error) {
    if (error instanceof MetaUnavailableError) {
      return <NoSnapshot />;
    }
    throw error;
  }

  return (
    <main className="mx-auto flex min-h-screen max-w-3xl flex-col gap-6 px-6 py-12">
      <header className="space-y-2">
        <Link href="/" className="text-xs text-slate-500 hover:text-slate-300">
          ← pokeHelper
        </Link>
        <h1 className="text-2xl font-bold">메타 현황</h1>
        <p className="text-sm text-slate-400">
          {data.snapshot.regulation} · {data.snapshot.format} · 상위 {data.entries.length}종
        </p>
      </header>

      {/* ★ 사용률 % 가 없는 이유를 화면에서 밝힌다 ★
          빠진 것을 설명 없이 두면 "왜 없지?" 가 아니라 "덜 만들었네" 로 읽힌다.
          추정과 사실을 구분한다 (CLAUDE.md 1-3). */}
      <p className="rounded border border-slate-800 bg-slate-900/50 px-3 py-2 text-xs text-slate-400">
        출처가 <strong className="text-slate-300">사용 순위</strong>만 제공하고 사용률
        퍼센트는 주지 않습니다. 없는 값을 0%로 채우지 않았습니다.
      </p>

      <UsageTable entries={data.entries} />

      {/* 라이선스 조건: 통계가 나오는 화면마다 출처 표기 (CLAUDE.md 8-2) */}
      <Attributions
        attributions={data.attributions}
        collectedAt={data.snapshot.collected_at}
        className="border-t border-slate-800 pt-4"
      />
    </main>
  );
}

function NoSnapshot() {
  return (
    <main className="mx-auto flex min-h-screen max-w-3xl flex-col justify-center gap-4 px-6 py-12">
      <h1 className="text-2xl font-bold">메타 현황</h1>
      {/* 오류가 아니라 상태다. 수집을 한 번도 안 했거나 검증 실패로 활성 전환이
          막혀 있다. 계산 기능은 이것과 무관하게 동작한다 (CLAUDE.md 8-5). */}
      <p className="text-sm text-slate-400">
        아직 수집된 메타가 없습니다. 계산 기능은 메타 없이도 동작합니다.
      </p>
      <Link href="/" className="text-sm text-slate-500 hover:text-slate-300">
        ← 돌아가기
      </Link>
    </main>
  );
}
