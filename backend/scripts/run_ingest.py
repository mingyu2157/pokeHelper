"""사용률 수집 실행 (Phase 2-6).

실행:
    docker compose exec backend uv run python -m scripts.run_ingest
    docker compose exec backend uv run python -m scripts.run_ingest --format Singles --limit 50

★ 이 스크립트가 스케줄러의 인터페이스다 ★

나중에 Airflow DAG 가 호출할 진입점이다. DAG 는 "언제, 몇 번 시도할지"만 알고
수집 로직은 모른다. 그래서 Airflow 를 걷어내도 수집은 그대로 돈다 — cron 으로도,
손으로도 돌릴 수 있다.

★ 종료 코드가 중요하다 ★

수집은 했는데 **검증 실패로 활성화하지 못한 경우가 가장 위험하다.** 데이터는
저장됐지만 서비스가 보는 메타는 갱신되지 않았다. 이걸 성공(0)으로 끝내면
스케줄러 UI 가 초록으로 뜨고, 매핑이 깨진 걸 아무도 모른 채 며칠이 지나간다.

    0  활성 스냅샷이 갱신됐다
    1  수집 자체가 실패했다 (네트워크·HTTP·형식)
    2  수집·적재는 됐지만 **활성화하지 못했다** — 사람이 봐야 한다
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import UTC, datetime
from typing import Final

from app.db.session import get_engine, get_session_factory
from app.ingestion.championsbattledata import ChampionsBattleDataSource, CollectionError
from app.repositories import snapshots
from app.services.ingest import ingest

DEFAULT_FORMAT: Final[str] = "Singles"
DEFAULT_LIMIT: Final[int] = 50

EXIT_OK: Final[int] = 0
EXIT_COLLECTION_FAILED: Final[int] = 1
EXIT_NOT_ACTIVATED: Final[int] = 2


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="챔피언스 사용률 수집")
    parser.add_argument("--format", dest="battle_format", default=DEFAULT_FORMAT)
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    parser.add_argument(
        "--regulation-code",
        default=None,
        help="생략하면 수집한 시즌 코드를 쓴다 (예: M7). 'Current' 같은 별칭은 들어가지 않는다.",
    )
    return parser.parse_args()


async def run(args: argparse.Namespace) -> int:
    source = ChampionsBattleDataSource()
    print(f"수집: {source.name} / {args.battle_format} / 상위 {args.limit}")
    print(f"  요청 간 1초 지연, 동시성 1 — 약 {args.limit}초 걸린다")

    try:
        result = await source.collect(battle_format=args.battle_format, limit=args.limit)
    except CollectionError as error:
        print(f"\n수집 실패 — {error}", file=sys.stderr)
        print("활성 스냅샷은 그대로다. 이전 데이터로 계속 서비스한다.", file=sys.stderr)
        return EXIT_COLLECTION_FAILED

    code = args.regulation_code or result.season
    print(f"  포켓몬 {len(result.entries)}종 수집 / 시즌 {result.season}")

    factory = get_session_factory()
    async with factory() as session:
        regulation = await snapshots.ensure_regulation(
            session, code=code, today=datetime.now(UTC).date()
        )
        report = await ingest(session, result=result, regulation_id=regulation.id)
        await session.commit()

    print(f"\n{report.summary()}")
    if report.skipped_categories:
        print(f"  검증 제외 항목: {', '.join(report.skipped_categories)} (한영 매핑 대기)")

    if report.activated:
        return EXIT_OK

    print("\n해석 실패 내역:", file=sys.stderr)
    for category, values in report.unresolved.items():
        print(f"  {category}: {len(values)}건 — {values[:10]}", file=sys.stderr)
    print(
        "\n스냅샷은 적재됐지만 활성화하지 않았다. 매핑을 고친 뒤 다시 돌린다.",
        file=sys.stderr,
    )
    return EXIT_NOT_ACTIVATED


async def main() -> int:
    try:
        return await run(parse_args())
    finally:
        await get_engine().dispose()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
