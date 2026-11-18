import type { Attribution } from "@/lib/api/meta";

/**
 * ★ 이 컴포넌트는 장식이 아니라 사용 조건이다 (CLAUDE.md 8-2) ★
 *
 * championsbattledata 라이선스는 "데이터를 표시하는 **모든 화면**에 출처
 * 표기와 링크"를 요구한다. 지키지 않으면 서비스를 내려야 한다.
 * 푸터 한 줄로 끝내지 않고, **통계가 나오는 화면마다** 붙인다.
 *
 * 그래서 `attributions` 에 기본값을 두지 않는다. 호출자가 넘기지 않으면
 * 타입 오류가 난다 — 백엔드 스키마가 필수 필드로 두는 것과 같은 장치다.
 */
export function Attributions({
  attributions,
  collectedAt,
  className = "",
}: {
  attributions: Attribution[];
  /** 수집 시각. 통계는 언제 모았는지에 따라 의미가 달라진다 (CLAUDE.md 1-2). */
  collectedAt?: string;
  className?: string;
}) {
  return (
    <div className={`space-y-1 text-xs text-slate-400 ${className}`}>
      {attributions.map((attribution) => (
        <p key={attribution.url}>
          {attribution.text} —{" "}
          <a
            className="underline decoration-dotted underline-offset-2 hover:text-slate-200"
            href={attribution.url}
            target="_blank"
            rel="noreferrer noopener"
          >
            {attribution.url.replace(/^https?:\/\//, "")}
          </a>
        </p>
      ))}
      {collectedAt && (
        <p>
          수집 시각 <time dateTime={collectedAt}>{formatCollectedAt(collectedAt)}</time>
        </p>
      )}
      <p>본 서비스는 포켓몬 공식 제휴가 아닙니다.</p>
    </div>
  );
}

/**
 * 수집 시각을 사람이 읽는 형태로.
 *
 * ★ 서버와 브라우저가 같은 문자열을 만들어야 한다 ★ `toLocaleString()` 을
 * 인자 없이 쓰면 서버(UTC 컨테이너)와 브라우저(KST)가 다른 문자열을 만들어
 * 하이드레이션 불일치가 난다. 시간대를 고정해 양쪽을 같게 만든다.
 */
function formatCollectedAt(value: string): string {
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return value;
  }
  return new Intl.DateTimeFormat("ko-KR", {
    timeZone: "Asia/Seoul",
    dateStyle: "medium",
    timeStyle: "short",
  }).format(parsed);
}
