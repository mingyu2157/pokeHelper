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

★ 1~3층은 **정확한 일치**를 요구하고, 4층만 **방향**으로 본다 (ADR-023).
  복원본이 앞선 것은 안전하고(ID 를 건너뛸 뿐), 뒤처진 것만 PK 충돌이다.
  자세한 근거는 아래 `_compare_sequences` 위의 주석에 있다.

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

#: 1~3층 — **정확히 같아야** 하는 것들. 값이 다르면 그대로 차이다.
LAYERS: Final[tuple[tuple[str, str], ...]] = (
    ("row_counts", "1 행 수"),
    ("checksums", "2 내용 체크섬"),
    ("constraints", "3 제약"),
    ("indexes", "3 인덱스"),
)

#: 4층 — ★ 정확한 일치가 아니라 **방향**으로 본다. 아래 `_compare_sequences` 참조.
SEQUENCE_LAYER: Final[str] = "4 시퀀스"

#: `pg_sequences.last_value` 가 NULL 일 때 `capture_state` 가 채우는 값.
UNUSED: Final[str] = "<unused>"


def compare(before: DatabaseState, after: DatabaseState) -> list[str]:
    """두 상태의 차이를 사람이 읽는 줄로 돌려준다. 같으면 빈 리스트.

    ★ 층마다 **사라진 것 / 생긴 것 / 값이 다른 것**을 따로 적는다.
      "다르다"만 적으면 복구가 덜 됐는지 더 됐는지 구분이 안 된다.

    ★ 4층만 판정 방식이 다르다 (ADR-023). `_compare_sequences` 를 읽어라.
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

    differences.extend(_compare_sequences(before, after))
    return differences


############################################################
# 4층 — ★ 방향으로 판정한다 (ADR-023)
############################################################
#
# 처음에는 1~3층과 똑같이 "값이 다르면 차이"로 봤다. **운영에서 첫 실행에 바로
# 거짓 양성이 났다** (2026-11-26, RDS PITR 리허설):
#
#   [4 시퀀스] 다름: meta_snapshots_id_seq 원본='3'   복원본='35'   (+32)
#   [4 시퀀스] 다름: pokemon_meta_id_seq   원본='150' 복원본='166'  (+16)
#
# PostgreSQL 의 `nextval` 은 32회마다만 WAL 에 기록하고, 그때 **현재값 + 32 를
# 미리** 적는다. 복구가 그 선행 값으로 돌아오므로 복원본은 최대 32 앞선다.
# 복구 결함이 아니라 설계다.
#
# ★ 그런데 이 층이 막으려던 것은 "값이 다른 것"이 아니라 **PK 충돌**이다.
#   앞선 시퀀스는 ID 를 건너뛸 뿐 충돌하지 않는다. 뒤처진 시퀀스만 터진다.
#   그래서 **판정 기준을 목적과 일치시킨다** — 방향만 본다.
#
# 매 복구마다 빨간불이 뜨면 운영자가 4층을 읽지 않게 되고, 그게 4층이 잡으려던
# 뒤처짐을 놓치는 길이다. 거짓 양성을 없애는 것이 이 층을 살리는 길이다.
#
# ★ 로컬 리허설(`tests/rehearsal/`)은 **여전히 정확한 일치를 요구한다.**
#   그쪽은 pg_dump/pg_restore 경로이고 `setval` 로 값을 그대로 복원하므로,
#   앞서는 일이 애초에 없다. 복구 방식이 다르면 올바름의 기준도 다르다.


def _sequence_position(value: str) -> int | None:
    """`last_value` 문자열을 순서 비교용 정수로. 읽을 수 없으면 None.

    ★ `<unused>` 는 **0** 으로 본다. 한 번도 쓰지 않은 시퀀스는 다음에 1 을
      내주므로, 원본이 3 인데 복원본이 `<unused>` 면 1 부터 다시 시작해
      **PK 충돌**이다. "같지 않다"가 아니라 "뒤처졌다"로 판정돼야 한다.
    """
    if value == UNUSED:
        return 0
    try:
        return int(value)
    except ValueError:
        return None


def _compare_sequences(before: DatabaseState, after: DatabaseState) -> list[str]:
    """뒤처진 시퀀스만 차이로 돌려준다. 앞선 것은 `sequence_advances` 로 간다."""
    differences: list[str] = []
    left, right = before.sequences, after.sequences

    for key in sorted(set(left) - set(right)):
        differences.append(f"[{SEQUENCE_LAYER}] 사라짐: {key} (원본={left[key]!r})")
    for key in sorted(set(right) - set(left)):
        differences.append(f"[{SEQUENCE_LAYER}] 생김: {key} (복원본={right[key]!r})")

    for key in sorted(set(left) & set(right)):
        source, restored = left[key], right[key]
        if source == restored:
            continue

        source_position = _sequence_position(source)
        restored_position = _sequence_position(restored)

        # ★ 읽을 수 없는 값은 통과시키지 않는다. 판정할 수 없으면 차이로 올린다 —
        #   조용히 넘기면 뒤처짐을 놓칠 수 있다.
        if source_position is None or restored_position is None:
            differences.append(
                f"[{SEQUENCE_LAYER}] 읽을 수 없음: {key} 원본={source!r} 복원본={restored!r}"
            )
        elif restored_position < source_position:
            differences.append(
                f"[{SEQUENCE_LAYER}] 뒤처짐: {key} 원본={source!r} 복원본={restored!r} "
                f"— 복구 직후 첫 INSERT 가 PK 충돌한다"
            )

    return differences


def sequence_advances(before: DatabaseState, after: DatabaseState) -> list[str]:
    """복원본에서 **앞선** 시퀀스를 사람이 읽는 줄로 돌려준다.

    ★ 차이가 아니므로 `compare` 가 아니라 여기서 돌려준다. 그래도 **버리지는
      않는다** — 정보를 숨기면 다음 사람이 같은 조사를 처음부터 반복한다.
      CLI 가 `참고` 로 따로 찍고, 종료 코드에는 영향을 주지 않는다.
    """
    notes: list[str] = []
    for key in sorted(set(before.sequences) & set(after.sequences)):
        source, restored = before.sequences[key], after.sequences[key]
        source_position = _sequence_position(source)
        restored_position = _sequence_position(restored)
        if source_position is None or restored_position is None:
            continue
        if restored_position > source_position:
            notes.append(f"{key}  {source} → {restored}  (+{restored_position - source_position})")
    return notes


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
    advances = sequence_advances(before, after)

    print(f"테이블 {len(before.row_counts)}개 / 행 {sum(before.row_counts.values())}건")
    print(
        f"제약 {len(before.constraints)}개 / 인덱스 {len(before.indexes)}개 "
        f"/ 시퀀스 {len(before.sequences)}개"
    )
    print()

    # ★ 차이가 아니지만 버리지 않는다 (ADR-023). 종료 코드에는 영향이 없다.
    if advances:
        print(f"참고 — 복원본에서 앞선 시퀀스 {len(advances)}개 (안전: PK 충돌 없음)")
        for note in advances:
            print(f"  {note}")
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
