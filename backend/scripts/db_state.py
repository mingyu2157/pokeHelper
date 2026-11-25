"""DB 상태를 네 층으로 포착하고 비교한다 (ADR-014 / ADR-020 결정 3).

★ 왜 `tests/` 가 아니라 `scripts/` 에 있는가 ★

원래 이 코드는 `tests/rehearsal/conftest.py` 안에 있었다. 로컬 복구 리허설만
쓰면 그 자리가 맞다. 그런데 ADR-020 세부사항 5 가 이렇게 약속했다 —

    운영판은 "스냅샷으로 새 인스턴스를 복원하고 **같은 4층을 비교**"로
    그대로 옮긴다. **검증되지 않은 DR 전략은 DR 전략이 아니다.**

운영 이미지는 `tests/` 를 복사하지 않는다(`backend/Dockerfile` 의 prod 스테이지).
그래서 "같은 4층"을 운영에서 재려면 셋 중 하나였다.

  1. 운영용을 따로 쓴다 → ★ **두 구현이 갈라진다.** 비교 기준이 둘이 되는
     순간 "복구가 맞다"는 말의 뜻이 환경마다 달라진다.
  2. `tests/` 를 운영 이미지에 넣는다 → 테스트 코드를 운영에 배포한다.
  3. **여기로 옮기고 리허설이 import 한다** → 진실의 원천 하나. `COPY ./scripts`
     가 이미 있으므로 운영 이미지에 자동으로 실린다.

3번을 골랐다. 로컬 리허설 33건이 그대로 통과하는 것이 추출이 성공했다는 증거다.

★ 왜 네 층인가 ★

| 층 | 잡는 것 |
|---|---|
| 1 행 수 | 통째로 빠진 테이블 |
| 2 내용 체크섬 | 행 수는 같고 **값이 깨진** 복구 |
| 3 제약·인덱스 | 데이터는 맞지만 **무결성 장치가 없는** DB |
| 4 시퀀스 | 복구 직후 첫 INSERT 가 **PK 충돌** |

행만 세는 비교는 2·3·4 를 전부 놓친다. 특히 3번은 **그 순간에는 아무 증상이
없다** — 다음에 잘못된 데이터가 들어올 때 비로소 드러난다.

실행 (운영 이미지 안에서):

    python -m scripts.db_state capture
    python -m scripts.db_state compare --target-host <복원된-엔드포인트>

★ `compare` 는 비밀번호를 인자로 받지 않는다. `DATABASE_URL` 에서 **호스트만**
바꿔 두 곳에 붙는다 — PITR 복원본은 같은 DB 의 같은 계정이고 엔드포인트만
다르기 때문이다. 명령줄에 비밀번호를 적으면 컨테이너 안 `ps` 에 보인다.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from dataclasses import asdict, dataclass
from typing import Any, Final
from urllib.parse import urlparse

PSQL_TIMEOUT_SECONDS: Final[int] = 180


@dataclass(frozen=True)
class DatabaseState:
    """복구 전/후를 비교하기 위한 DB 스냅샷.

    행만 담지 않는다. **인덱스·제약·시퀀스까지** 담는 게 요점이다 —
    행은 맞는데 무결성 장치가 사라진 복구가 가장 위험하다.
    """

    row_counts: dict[str, int]
    checksums: dict[str, str]
    sequences: dict[str, str]
    indexes: dict[str, str]
    constraints: dict[str, str]

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, indent=1, sort_keys=True)

    @classmethod
    def from_json(cls, text: str) -> DatabaseState:
        data: dict[str, Any] = json.loads(text)
        return cls(
            row_counts={k: int(v) for k, v in data["row_counts"].items()},
            checksums=dict(data["checksums"]),
            sequences=dict(data["sequences"]),
            indexes=dict(data["indexes"]),
            constraints=dict(data["constraints"]),
        )


def psql(sql: str, *, env: dict[str, str], check: bool = True) -> subprocess.CompletedProcess[str]:
    """psql 로 SQL 한 덩어리를 실행한다. 결과는 탭 구분 평문으로 받는다.

    ★ `env` 에 기본값을 두지 않는다. 기본값이 있으면 **어느 DB 를 봤는지
      호출부에서 안 보이고**, 이 함수는 DROP 이 도는 리허설에서도 쓰인다.
    """
    return subprocess.run(
        [
            "psql",
            "--no-psqlrc",
            "--quiet",
            "--tuples-only",
            "--no-align",
            "--field-separator=\t",
            "--set=ON_ERROR_STOP=1",
            "--command",
            sql,
        ],
        env=env,
        capture_output=True,
        text=True,
        check=check,
        timeout=PSQL_TIMEOUT_SECONDS,
    )


def scalar(sql: str, *, env: dict[str, str]) -> str:
    return psql(sql, env=env).stdout.strip()


def rows(sql: str, *, env: dict[str, str]) -> list[list[str]]:
    out = psql(sql, env=env).stdout.strip()
    if not out:
        return []
    return [line.split("\t") for line in out.splitlines()]


def table_names(env: dict[str, str]) -> list[str]:
    return [
        r[0]
        for r in rows(
            "SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename",
            env=env,
        )
    ]


def capture_state(env: dict[str, str]) -> DatabaseState:
    """네 층을 한 번에 포착한다."""
    tables = table_names(env)

    row_counts: dict[str, int] = {}
    checksums: dict[str, str] = {}
    for table in tables:
        row_counts[table] = int(scalar(f'SELECT count(*) FROM public."{table}"', env=env))
        # 행을 텍스트로 직렬화해 정렬 후 md5. 행 수가 같고 값이 깨진 복구를 잡는다.
        checksums[table] = scalar(
            f"SELECT coalesce(md5(string_agg(t::text, '|' ORDER BY t::text)), '') "
            f'FROM public."{table}" t',
            env=env,
        )

    sequences = {
        r[0]: (r[1] if len(r) > 1 else "")
        for r in rows(
            "SELECT sequencename, coalesce(last_value::text, '<unused>') "
            "FROM pg_sequences WHERE schemaname='public' ORDER BY sequencename",
            env=env,
        )
    }
    indexes = {
        r[0]: r[1]
        for r in rows(
            "SELECT indexname, indexdef FROM pg_indexes "
            "WHERE schemaname='public' ORDER BY indexname",
            env=env,
        )
    }
    constraints = {
        r[0]: r[1]
        for r in rows(
            "SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint "
            "WHERE connamespace = 'public'::regnamespace ORDER BY conname",
            env=env,
        )
    }
    return DatabaseState(row_counts, checksums, sequences, indexes, constraints)


############################################################
# 비교 — ★ 순수 함수다. DB 없이 테스트할 수 있다
############################################################

LAYERS: Final[tuple[tuple[str, str], ...]] = (
    ("row_counts", "1 행 수"),
    ("checksums", "2 내용 체크섬"),
    ("constraints", "3 제약"),
    ("indexes", "3 인덱스"),
    ("sequences", "4 시퀀스"),
)


def compare(before: DatabaseState, after: DatabaseState) -> list[str]:
    """두 상태의 차이를 사람이 읽는 줄로 돌려준다. 같으면 빈 리스트.

    ★ 층마다 **사라진 것 / 생긴 것 / 값이 다른 것**을 따로 적는다.
      "다르다"만 적으면 복구가 덜 됐는지 더 됐는지 구분이 안 된다.
    """
    differences: list[str] = []

    for field, label in LAYERS:
        left: dict[str, Any] = getattr(before, field)
        right: dict[str, Any] = getattr(after, field)

        for key in sorted(set(left) - set(right)):
            differences.append(f"[{label}] 사라짐: {key} (원본={left[key]!r})")
        for key in sorted(set(right) - set(left)):
            differences.append(f"[{label}] 생김: {key} (복원본={right[key]!r})")
        for key in sorted(set(left) & set(right)):
            if left[key] != right[key]:
                differences.append(
                    f"[{label}] 다름: {key} 원본={left[key]!r} 복원본={right[key]!r}"
                )

    return differences


############################################################
# 접속 환경 — ★ DSN 에서 만들고, 호스트만 바꿀 수 있다
############################################################


def env_from_dsn(dsn: str, *, host_override: str | None = None) -> dict[str, str]:
    """SQLAlchemy DSN 을 libpq 환경변수로 바꾼다.

    ★ `host_override` 가 이 함수의 존재 이유다. PITR 복원본은 **같은 DB 의 같은
      계정**이고 엔드포인트만 다르다. 호스트만 갈아끼우면 비밀번호를 명령줄에
      적지 않고도 두 곳에 붙을 수 있다.
    """
    parsed = urlparse(dsn.replace("+asyncpg", ""))
    host = host_override or parsed.hostname
    if not host:
        raise ValueError(f"DSN 에서 호스트를 읽을 수 없다: {dsn!r}")
    if not parsed.username:
        raise ValueError("DSN 에 사용자명이 없다")

    env = os.environ.copy()
    env.update(
        {
            "PGHOST": host,
            "PGPORT": str(parsed.port or 5432),
            "PGUSER": parsed.username,
            "PGPASSWORD": parsed.password or "",
            "PGDATABASE": (parsed.path or "/postgres").lstrip("/"),
        }
    )
    return env


def _require_database_url() -> str:
    dsn = os.environ.get("DATABASE_URL", "")
    if not dsn:
        raise SystemExit("DATABASE_URL 이 비어 있다. 이 명령은 태스크 안에서 돈다.")
    return dsn


def _capture(_: argparse.Namespace) -> int:
    env = env_from_dsn(_require_database_url())
    print(capture_state(env).to_json())
    return 0


def _compare(args: argparse.Namespace) -> int:
    dsn = _require_database_url()
    source_env = env_from_dsn(dsn)
    target_env = env_from_dsn(dsn, host_override=args.target_host)

    print(f"원본   : {source_env['PGHOST']}")
    print(f"복원본 : {target_env['PGHOST']}")
    print()

    before = capture_state(source_env)
    after = capture_state(target_env)
    differences = compare(before, after)

    print(f"테이블 {len(before.row_counts)}개 / 행 {sum(before.row_counts.values())}건")
    print(
        f"제약 {len(before.constraints)}개 / 인덱스 {len(before.indexes)}개 "
        f"/ 시퀀스 {len(before.sequences)}개"
    )
    print()

    if not differences:
        print("네 층 모두 일치한다.")
        return 0

    # ★ 차이를 전부 찍는다. 앞 몇 개만 보여주면 "이것만 다르다"로 읽힌다.
    print(f"★ 차이 {len(differences)}건")
    for line in differences:
        print(f"  {line}")
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(description="DB 상태를 네 층으로 포착·비교한다")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("capture", help="DATABASE_URL 의 상태를 JSON 으로 찍는다")

    compare_parser = sub.add_parser(
        "compare", help="DATABASE_URL 과 호스트만 다른 DB 를 네 층으로 비교한다"
    )
    compare_parser.add_argument(
        "--target-host",
        required=True,
        help="복원된 인스턴스의 엔드포인트 호스트명 (포트 없이)",
    )

    args = parser.parse_args()
    handler = {"capture": _capture, "compare": _compare}[args.command]
    return handler(args)


if __name__ == "__main__":
    sys.exit(main())
