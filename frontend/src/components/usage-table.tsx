import Link from "next/link";
import type { UsageEntry } from "@/lib/api/meta";

/**
 * 사용 순위표.
 *
 * ★ "사용률 %" 열이 없는 것은 의도다 ★
 *
 * 출처 API 가 순위만 주고 사용률 퍼센트를 주지 않는다. 없는 열을 만들어
 * 0% 나 빈 칸을 띄우면 **없는 사실을 있는 것처럼** 보여주게 된다.
 * 주는 것(순위)만 띄운다.
 */
export function UsageTable({ entries }: { entries: UsageEntry[] }) {
  return (
    <table className="w-full border-collapse text-sm">
      <caption className="sr-only">포켓몬 사용 순위</caption>
      <thead>
        <tr className="border-b border-slate-800 text-left text-slate-400">
          <th scope="col" className="w-16 py-2 font-medium">
            순위
          </th>
          <th scope="col" className="py-2 font-medium">
            포켓몬
          </th>
          <th scope="col" className="w-24 py-2 text-right font-medium">
            변동
          </th>
        </tr>
      </thead>
      <tbody>
        {entries.map((entry) => (
          <tr key={entry.pokemon_id} className="border-b border-slate-900 hover:bg-slate-900/60">
            <td className="py-2 tabular-nums text-slate-400">{entry.rank ?? "-"}</td>
            <td className="py-2">
              <Link
                className="underline decoration-slate-700 underline-offset-4 hover:decoration-slate-400"
                href={`/meta/${encodeURIComponent(entry.pokemon_id)}`}
              >
                {entry.name_ko ?? <UnmappedName id={entry.pokemon_id} />}
              </Link>
            </td>
            <td className="py-2 text-right">
              <RankChange value={entry.rank_change} />
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

/**
 * 순위 변동. 양수가 상승이다.
 *
 * 색만으로 구분하지 않고 기호(▲▼)를 함께 쓴다 — 색각 이상이 있으면
 * 빨강/초록 구분이 안 된다.
 */
function RankChange({ value }: { value: number | null }) {
  if (value === null) {
    return <span className="text-slate-600">신규</span>;
  }
  if (value === 0) {
    return <span className="text-slate-600">-</span>;
  }
  const up = value > 0;
  return (
    <span className={up ? "text-emerald-400" : "text-rose-400"}>
      {up ? "▲" : "▼"} {Math.abs(value)}
    </span>
  );
}

/**
 * ★ 한글명 매핑이 없을 때 (CLAUDE.md 8-4) ★
 *
 * 영문 id 를 한글명 자리에 그냥 띄우면 사용자는 그게 **우리가 고른 표기**라고
 * 읽는다. 미매핑임을 눈에 보이게 적고, 원문을 그 옆에 둔다.
 */
export function UnmappedName({ id }: { id: string }) {
  return (
    <span className="text-slate-400">
      <code className="rounded bg-slate-900 px-1 py-0.5 text-xs">{id}</code>
      <span className="ml-1 text-xs text-amber-500/80">한글명 미등록</span>
    </span>
  );
}
