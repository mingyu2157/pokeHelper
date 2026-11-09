"""한영 명칭 조회.

데이터는 `backend/data/name_map.json` 에 있고 생성 스크립트가 만든다.
**domain 은 파일을 읽지 않는다** (ADR-007) — 바깥 계층이 읽어 주입한다.

★ 이 계층의 규칙은 하나다: 모르면 실패한다 ★

ADR-005 가 정한 fail-closed 다. 매핑이 없을 때 영문명을 그대로 돌려주거나 비슷한 걸
찾아주면, 화면에는 그럴듯한 글자가 뜨고 그 뒤에서 엉뚱한 포켓몬의 종족값으로 계산이
돌아간다. 사용자도 모르고 우리도 모른다. **그래서 추측하지 않고 예외를 던진다.**

- 퍼지/유사도 매칭은 없다. 정확 일치 조회만 한다.
- 대소문자 정규화도 하지 않는다. id 는 생성기가 만든 형태 그대로다.
- 양방향(`ko -> id`, `id -> ko`) 유일성을 생성 시점에 검사하고, 여기서 한 번 더 막는다.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from app.domain.exceptions import DuplicateNameMapping, NameNotMapped


@dataclass(frozen=True)
class NameMap:
    """showdown_id <-> 한국 서버 명칭. 양방향 모두 유일하다."""

    _ko_by_id: Mapping[str, str]
    _id_by_ko: Mapping[str, str]

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Mapping[str, Any]]) -> NameMap:
        ko_by_id: dict[str, str] = {}
        id_by_ko: dict[str, str] = {}
        for showdown_id, record in raw.items():
            korean = str(record["ko"]).strip()
            if not korean:
                raise DuplicateNameMapping(f"{showdown_id}: 한글명이 비어 있다")
            if korean in id_by_ko:
                raise DuplicateNameMapping(
                    f"한글명 {korean!r} 가 두 id 에 걸려 있다: "
                    f"{id_by_ko[korean]!r}, {showdown_id!r}"
                )
            ko_by_id[showdown_id] = korean
            id_by_ko[korean] = showdown_id
        return cls(_ko_by_id=ko_by_id, _id_by_ko=id_by_ko)

    def ko(self, showdown_id: str) -> str:
        """id -> 한글명. 매핑이 없으면 실패한다 (영문명을 통과시키지 않는다)."""
        try:
            return self._ko_by_id[showdown_id]
        except KeyError as error:
            raise NameNotMapped(
                f"한글명이 매핑되지 않은 포켓몬: {showdown_id!r}. "
                "name_map_pending.json 을 확인한다."
            ) from error

    def id_of(self, korean: str) -> str:
        """한글명 -> id. 사용자 입력을 받을 때 쓴다. 비슷한 이름을 찾아주지 않는다."""
        try:
            return self._id_by_ko[korean]
        except KeyError as error:
            raise NameNotMapped(f"모르는 한글 명칭: {korean!r}") from error

    def has(self, showdown_id: str) -> bool:
        """예외 없이 존재만 확인한다. 커버리지 검사용."""
        return showdown_id in self._ko_by_id

    def ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._ko_by_id))
