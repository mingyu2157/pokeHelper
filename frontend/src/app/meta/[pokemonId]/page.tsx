import Link from "next/link";
import { notFound } from "next/navigation";
import { Attributions } from "@/components/attribution";
import { UnmappedName } from "@/components/usage-table";
import type { OptionUsage, SpreadUsage } from "@/lib/api/meta";
import { MetaUnavailableError, fetchPokemonMeta } from "@/lib/api/meta";

export const dynamic = "force-dynamic";

const CATEGORY_LABELS: Record<string, string> = {
  moves: "기술",
  items: "도구",
  abilities: "특성",
  natures: "성격",
  teammates: "함께 쓰인 포켓몬",
};

export default async function PokemonMetaPage({
  params,
}: {
  params: Promise<{ pokemonId: string }>;
}) {
  const { pokemonId } = await params;

  let data;
  try {
    data = await fetchPokemonMeta(pokemonId);
  } catch (error) {
    if (error instanceof MetaUnavailableError) {
      notFound();
    }
    throw error;
  }

  return (
    <main className="mx-auto flex min-h-screen max-w-3xl flex-col gap-6 px-6 py-12">
      <header className="space-y-2">
        <Link href="/meta" className="text-xs text-slate-500 hover:text-slate-300">
          ← 메타 현황
        </Link>
        <h1 className="text-2xl font-bold">
          {data.name_ko ?? <UnmappedName id={data.pokemon_id} />}
        </h1>
        <p className="text-sm text-slate-400">
          {data.rank !== null ? `사용 순위 ${data.rank}위` : "순위 정보 없음"} ·{" "}
          {data.snapshot.regulation} · {data.snapshot.format}
        </p>
      </header>

      <Spreads spreads={data.spreads} />

      {Object.entries(data.options).map(([category, options]) => (
        <OptionSection
          key={category}
          label={CATEGORY_LABELS[category] ?? category}
          options={options}
          unmapped={data.unmapped[category as keyof typeof data.unmapped] ?? 0}
        />
      ))}

      <Attributions
        attributions={data.attributions}
        collectedAt={data.snapshot.collected_at}
        className="border-t border-slate-800 pt-4"
      />
    </main>
  );
}

/**
 * 노력치 배분. HABCDS 표기는 **백엔드가 만든다** — 같은 표기를 두 곳에서
 * 조립하면 거기서 어긋난다 (CLAUDE.md 4장).
 */
function Spreads({ spreads }: { spreads: SpreadUsage[] }) {
  if (spreads.length === 0) {
    return null;
  }
  return (
    <section className="space-y-2">
      <h2 className="text-sm font-semibold text-slate-300">노력치 배분</h2>
      <ul className="space-y-1">
        {spreads.slice(0, 5).map((spread) => (
          <li key={spread.rank} className="flex items-baseline gap-3 text-sm">
            <code className="rounded bg-slate-900 px-2 py-0.5 font-mono text-slate-200">
              {spread.notation}
            </code>
            <Rate value={spread.rate} />
          </li>
        ))}
      </ul>
      <p className="text-xs text-slate-500">
        챔피언스 표기입니다 — 스탯당 0~32, 총합 66.
      </p>
    </section>
  );
}

function OptionSection({
  label,
  options,
  unmapped,
}: {
  label: string;
  options: OptionUsage[];
  unmapped: number;
}) {
  if (options.length === 0) {
    return null;
  }
  return (
    <section className="space-y-2">
      <h2 className="flex items-baseline gap-2 text-sm font-semibold text-slate-300">
        {label}
        {/* ★ 미매핑을 숨기지 않는다 ★ 숨기면 사용자는 영문 id 를 우리가 고른
            표기로 오해한다. 진행 상황으로 보여주는 게 정직하다. */}
        {unmapped > 0 && (
          <span className="text-xs font-normal text-amber-500/80">
            한글명 미등록 {unmapped}건
          </span>
        )}
      </h2>
      <ul className="space-y-1">
        {options.slice(0, 8).map((option) => (
          <li key={option.option_id} className="flex items-baseline gap-3 text-sm">
            <span className="min-w-0 flex-1 truncate">
              {option.name_ko ?? <UnmappedName id={option.option_id} />}
            </span>
            <Rate value={option.rate} />
          </li>
        ))}
      </ul>
    </section>
  );
}

/**
 * 채용률.
 *
 * ★ null 을 0% 로 띄우지 않는다 ★ 파트너는 순위만 주고 비율을 주지 않는다.
 * "채용률 0%" 와 "채용률을 모른다" 는 다른 사실이다.
 */
function Rate({ value }: { value: number | null }) {
  if (value === null) {
    return <span className="text-xs text-slate-600">비율 미제공</span>;
  }
  return (
    <span className="tabular-nums text-slate-400">{value.toFixed(1)}%</span>
  );
}
