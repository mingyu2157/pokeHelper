"""보존 기한이 지난 스냅샷 정리 (Phase 2-6).

실행:
    docker compose exec backend uv run python -m scripts.cleanup_snapshots
    docker compose exec backend uv run python -m scripts.cleanup_snapshots --dry-run

★ 왜 지워야 하는가 ★

championsbattledata 약관상 **영구 미러/아카이브를 만들 수 없다** (CLAUDE.md 8-2).
캐싱은 허용되지만 캐시가 원본의 영구 대체재가 되면 안 된다. 스냅샷을 무한 누적하면
그게 아카이브다. 보존 기한(`expires_at`)이 그 장치이고, 이 스크립트가 집행한다.

★ 활성 스냅샷은 만료돼도 지우지 않는다 ★

지우면 서비스가 볼 메타가 사라진다. 수집이 며칠 멈춰도 마지막 스냅샷으로 계속
서비스해야 한다 (CLAUDE.md 8-5). 그래서 "만료 + 비활성"만 지운다.
즉 **보존 기한은 '이 데이터를 더 안 쓴다'가 아니라 '쌓아두지 않는다'는 뜻**이다.
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import UTC, datetime
from typing import Final

from sqlalchemy import func, select

from app.db.models import MetaSnapshot
from app.db.session import get_engine, get_session_factory
from app.repositories import snapshots

EXIT_OK: Final[int] = 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="만료 스냅샷 정리")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="지우지 않고 대상만 보여준다",
    )
    return parser.parse_args()


async def run(args: argparse.Namespace) -> int:
    now = datetime.now(UTC)
    factory = get_session_factory()

    async with factory() as session:
        total = await session.scalar(select(func.count()).select_from(MetaSnapshot))
        targets = (
            await session.execute(
                select(MetaSnapshot.id, MetaSnapshot.format, MetaSnapshot.expires_at).where(
                    MetaSnapshot.expires_at < now, MetaSnapshot.is_active.is_(False)
                )
            )
        ).all()

        print(f"스냅샷 {total}개 / 만료·비활성 {len(targets)}개")
        for snapshot_id, battle_format, expires_at in targets[:10]:
            print(f"  #{snapshot_id} {battle_format} 만료 {expires_at:%Y-%m-%d %H:%M}")

        if args.dry_run:
            print("\n--dry-run 이라 지우지 않았다.")
            return EXIT_OK

        deleted = await snapshots.delete_expired(session, now=now)
        await session.commit()

    print(f"\n{deleted}개 삭제. 활성 스냅샷은 만료돼도 남긴다 (서비스가 볼 메타).")
    return EXIT_OK


async def main() -> int:
    try:
        return await run(parse_args())
    finally:
        await get_engine().dispose()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
