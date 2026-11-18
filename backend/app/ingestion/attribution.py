"""출처 표기 레지스트리 (CLAUDE.md 8-2, ADR-013).

★ 왜 별도 모듈인가 ★

출처 표기는 **수집할 때만 필요한 게 아니라 표시할 때 필요하다.** 라이선스가
요구하는 것은 "데이터를 표시하는 모든 화면에 출처 표기와 링크"이기 때문이다.
그런데 읽기 경로(저장소 → 서비스 → API)는 `meta_snapshots.source` 라는
**문자열 하나**만 알고, 어느 어댑터가 그걸 넣었는지 모른다.

읽기 서비스가 `championsbattledata.py` 를 import 하면 사이트별 구현이 서비스
계층으로 새어 나온다 (CLAUDE.md 8-6 이 금지한다). 그래서 **source 문자열 →
표기** 변환만 떼어 여기 둔다. 수집 어댑터도 읽기 서비스도 이 레지스트리를
본다. 서비스는 끝까지 사이트 이름을 모른다.

★ 조회는 fail-closed 다 ★

모르는 출처는 `None` 이나 빈 문자열을 돌려주지 않고 **예외를 던진다.**
표기 없이 통계를 내보내는 것이 라이선스 위반이고, 그 위반은 조용히 일어난다 —
화면에는 숫자가 멀쩡히 나오고 출처 줄만 비어 있을 뿐이다. 눈에 띄지 않는
위반이라 코드가 막아야 한다. `CollectionResult.attribution` 에 기본값을 두지
않은 것과 같은 이유다 (ADR-013).
"""

from __future__ import annotations

from typing import Final

from app.ingestion.base import Attribution

#: championsbattledata 공개 API. 라이선스가 요구하는 문구이므로 임의로 바꾸지 않는다.
CHAMPIONSBATTLEDATA: Final[Attribution] = Attribution(
    text="Battle data provided by Pokémon Champions Battle Data",
    url="https://championsbattledata.com",
)

#: op.gg — 기술 위력·명중·PP 시드의 출처 (ADR-009).
#:
#: ★ 아직 어떤 응답에도 붙지 않는다 ★ 지금 메타 화면이 보여주는 것은 기술
#: **채용률**(championsbattledata)이지 기술 **수치**가 아니다. 화면에 위력·명중이
#: 올라가는 순간 이 표기가 **함께** 올라가야 한다. 그때 빠뜨리지 않도록 미리
#: 등록해 둔다 — 쓸 때 찾아서 만들면 급할 때 빠진다.
OPGG: Final[Attribution] = Attribution(
    text="Move data from op.gg",
    url="https://op.gg",
)

_REGISTRY: Final[dict[str, Attribution]] = {
    "championsbattledata": CHAMPIONSBATTLEDATA,
    "opgg": OPGG,
}


class UnknownSourceError(Exception):
    """출처 표기를 모르는 출처다. 표기 없이 내보내느니 실패한다."""


def for_source(source: str) -> Attribution:
    """출처 문자열에 대응하는 표기. 모르면 예외를 던진다.

    두 번째 출처를 붙이는 사람이 여기 등록을 빠뜨리면 **그 출처의 데이터가
    처음 화면에 나가려는 순간 실패한다.** 출처 표기 없이 배포되는 것보다 낫다.
    """
    try:
        return _REGISTRY[source]
    except KeyError:
        raise UnknownSourceError(
            f"출처 '{source}' 의 표기가 등록되지 않았다. "
            "app/ingestion/attribution.py 에 추가해야 한다 — "
            "표기 없이 통계를 표시하면 라이선스 위반이다 (CLAUDE.md 8-2)."
        ) from None


def known_sources() -> tuple[str, ...]:
    """등록된 출처 이름들. 테스트와 진단용이다."""
    return tuple(sorted(_REGISTRY))
