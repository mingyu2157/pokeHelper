"""learnset 조회 — 이 포켓몬이 이 기술을 배울 수 있는가.

데이터는 `backend/data/learnsets.json` 에 있고 생성 스크립트가 만든다.
**domain 은 파일을 읽지 않는다** (ADR-007) — 바깥 계층이 읽어 주입한다.

★ 왜 필요한가 ★

파티 빌더(Phase 5)가 이걸 검증하지 않으면 사용자가 **배울 수 없는 기술이 들어간
파티를 저장**한다. 그러면 그 파티로 계산한 데미지가 나오고, 사용자는 그 수치를
근거라고 믿는다. 이 서비스의 존재 이유가 "모든 출력에 근거가 붙는다"인데,
근거의 전제가 거짓이면 그 위의 모든 수치가 의미를 잃는다.

★ 모르는 포켓몬은 '못 배운다'가 아니라 예외다 ★

`can_learn("mystery", "earthquake")` 가 False 를 돌려주면, 호출자는 "그 포켓몬은
지진을 못 배운다"고 읽는다. 실제로는 **그 포켓몬을 모르는 것**이다. 둘을 섞으면
오타 하나가 "학습 불가"로 둔갑해 조용히 파티에서 기술이 빠진다.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from app.domain.exceptions import UnknownPokemon


@dataclass(frozen=True)
class Learnset:
    """showdown_id -> 배울 수 있는 기술 id 집합."""

    _by_pokemon: Mapping[str, frozenset[str]]

    @classmethod
    def from_mapping(cls, raw: Mapping[str, list[str]]) -> Learnset:
        return cls(
            _by_pokemon={showdown_id: frozenset(move_ids) for showdown_id, move_ids in raw.items()}
        )

    def can_learn(self, showdown_id: str, move_id: str) -> bool:
        """모르는 포켓몬은 False 가 아니라 예외다. 둘은 다른 사실이다."""
        return move_id in self.moves_of(showdown_id)

    def moves_of(self, showdown_id: str) -> frozenset[str]:
        try:
            return self._by_pokemon[showdown_id]
        except KeyError as error:
            raise UnknownPokemon(
                f"learnset 에 없는 포켓몬: {showdown_id!r}. '못 배운다'와 '모른다'는 다르다."
            ) from error

    def unlearnable(self, showdown_id: str, move_ids: tuple[str, ...]) -> tuple[str, ...]:
        """배울 수 없는 기술만 골라 돌려준다. 파티 검증에서 쓴다.

        불린 하나가 아니라 목록을 주는 이유는, 사용자에게 **어느 기술이** 문제인지
        말해줘야 하기 때문이다. "이 파티는 불가능합니다"만으로는 고칠 수 없다.
        """
        learnable = self.moves_of(showdown_id)
        return tuple(move_id for move_id in move_ids if move_id not in learnable)

    def ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._by_pokemon))

    def pair_count(self) -> int:
        return sum(len(moves) for moves in self._by_pokemon.values())
