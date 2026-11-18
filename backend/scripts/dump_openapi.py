"""OpenAPI 스키마를 파일로 떨어뜨린다.

실행:
    docker compose exec backend uv run python -m scripts.dump_openapi
    docker compose exec backend uv run python -m scripts.dump_openapi --check

★ 왜 서버를 띄우지 않고 파일로 떨어뜨리는가 ★

프론트 타입은 OpenAPI 에서 자동 생성한다 (CLAUDE.md 6장). 생성기가 돌 때
백엔드가 떠 있어야 한다면 **CI 에서 쓸 수 없다** — 프론트 잡에는 백엔드도
DB 도 없다. 스키마를 파일로 커밋해 두면 두 잡이 서로를 기다리지 않는다.

★ `--check` 가 이 스크립트의 핵심이다 ★

스키마를 바꿔놓고 파일을 갱신하지 않으면, 프론트는 **옛 스키마로 만든 타입**을
계속 쓰면서 타입체크를 통과한다. 백엔드는 바뀌었는데 프론트는 모른다 —
실패가 런타임까지 미뤄진다. `--check` 는 커밋된 파일과 지금 코드가 만드는
스키마가 **같은지** 보고, 다르면 0 이 아닌 코드로 끝난다.

    0  파일이 최신이다
    1  코드와 파일이 다르다 (재생성하고 커밋해야 한다)
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Final

from app.main import app

#: 저장소에 커밋하는 스키마 파일. 프론트 생성기가 이 파일을 읽는다.
OPENAPI_PATH: Final[Path] = Path(__file__).resolve().parents[1] / "openapi.json"

EXIT_OK: Final[int] = 0
EXIT_STALE: Final[int] = 1


def current_schema() -> dict[str, Any]:
    return app.openapi()


def serialize(schema: dict[str, Any]) -> str:
    """키를 정렬해 직렬화한다.

    정렬하지 않으면 파이썬 dict 순서가 바뀔 때마다 **내용이 같은데도** 파일이
    달라지고, `--check` 가 거짓으로 실패한다. 생성물의 안정성은 그 생성물을
    비교 대상으로 쓰는 순간 필수 조건이 된다.
    """
    return json.dumps(schema, indent=2, ensure_ascii=False, sort_keys=True) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="OpenAPI 스키마를 파일로 떨어뜨린다")
    parser.add_argument(
        "--check",
        action="store_true",
        help="쓰지 않고 커밋된 파일과 비교만 한다. 다르면 1 로 끝난다.",
    )
    args = parser.parse_args(argv)

    rendered = serialize(current_schema())

    if args.check:
        if not OPENAPI_PATH.exists():
            print(f"{OPENAPI_PATH} 가 없다. 먼저 생성하고 커밋해야 한다.", file=sys.stderr)
            return EXIT_STALE
        if OPENAPI_PATH.read_text(encoding="utf-8") != rendered:
            print(
                f"{OPENAPI_PATH.name} 이 현재 스키마와 다르다.\n"
                "  uv run python -m scripts.dump_openapi\n"
                "를 돌리고 함께 커밋해야 한다 — 프론트 타입이 옛 스키마로 남는다.",
                file=sys.stderr,
            )
            return EXIT_STALE
        print(f"{OPENAPI_PATH.name} 최신 상태")
        return EXIT_OK

    OPENAPI_PATH.write_text(rendered, encoding="utf-8", newline="\n")
    print(OPENAPI_PATH)
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
