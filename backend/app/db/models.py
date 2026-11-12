"""DB 테이블 정의 (Phase 2-4).

★ 마스터 테이블은 `data/*.json` 의 **파생물**이다 (ADR-010) ★

진실의 원천은 JSON 시드다. 이 테이블들은 `scripts/load_seed_to_db.py` 가 JSON 을 읽어
채운다. 언제든 비우고 다시 적재할 수 있어야 하고, **손으로 UPDATE 하지 않는다.**
계산 엔진(`app/domain/`)은 이 테이블을 쓰지 않는다 — JSON 카탈로그를 직접 받는다.
그래서 DB 가 비어 있어도 데미지 계산은 그대로 돌아간다 (CLAUDE.md 1-4).

★ 기본키는 showdown 식 문자열 id 다 ★

`dex_no` 를 쓰지 않는다. championsbattledata 가 모든 폼에 `dex_number: null` 을 주고
(Phase 2-1 확인), 사용률 API·우리 JSON·한영 매핑이 전부 `showdown_id` 로 조인한다.
정수 대리키를 두면 ETL 단계마다 "어느 id 로 참조하지?"를 묻게 되고, 그 변환 지점이
전부 버그 후보가 된다.

★ 메타 통계는 보존 기한이 있는 캐시다 ★

championsbattledata 약관상 영구 미러/아카이브를 만들 수 없다 (CLAUDE.md 8-2).
`meta_snapshots.expires_at` 이 그 장치이고, 정리 배치(Phase 2-6)가 만료분을 지운다.
무한 누적 테이블을 만들지 않는다.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

# Postgres 에서는 JSONB 를, 그 외(테스트용 SQLite 등)에서는 JSON 을 쓴다.
JsonType = JSON().with_variant(JSONB(), "postgresql")


class Regulation(Base):
    """레귤레이션(시즌별 규정). 모든 통계의 스코프다."""

    __tablename__ = "regulations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    started_at: Mapped[date] = mapped_column(Date, nullable=False)
    ended_at: Mapped[date | None] = mapped_column(Date, nullable=True)
    # 레벨·금지 포켓몬·기믹 허용 여부. 규정이 시즌마다 달라 컬럼으로 고정하지 않는다.
    rules: Mapped[dict[str, Any]] = mapped_column(JsonType, nullable=False, default=dict)


class Pokemon(Base):
    """포켓몬 마스터. `data/pokemon.json` + `data/name_map.json` 에서 적재한다."""

    __tablename__ = "pokemon"

    showdown_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name_en: Mapped[str] = mapped_column(Text, nullable=False)
    # 한글명의 원천은 name_map.json 이다 (ADR-005). 여기 있는 건 조회 편의를 위한
    # 비정규화 사본이고, 적재 시 매핑에서 채운다.
    name_ko: Mapped[str] = mapped_column(Text, nullable=False)
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    form_kind: Mapped[str] = mapped_column(Text, nullable=False, default="Base")

    type1: Mapped[str] = mapped_column(String(16), nullable=False)
    type2: Mapped[str | None] = mapped_column(String(16), nullable=True)

    base_hp: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    base_atk: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    base_def: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    base_spa: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    base_spd: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    base_spe: Mapped[int] = mapped_column(SmallInteger, nullable=False)

    # 특성은 영문 id 배열로 둔다. 포켓몬당 1~3개뿐이라 중간 테이블이 과하고,
    # 특성 효과 데이터가 아직 없어 조인할 내용도 없다.
    ability_ids: Mapped[list[str]] = mapped_column(JsonType, nullable=False, default=list)

    __table_args__ = (
        CheckConstraint("base_hp BETWEEN 1 AND 255", name="ck_pokemon_base_hp"),
        CheckConstraint("type1 <> type2", name="ck_pokemon_types_differ"),
        Index("ix_pokemon_name_ko", "name_ko"),
    )


class Move(Base):
    """기술 마스터. `data/moves.json` 에서 적재한다.

    수치가 본가와 다르다 (ADR-009). 본가 데이터로 덮어쓰면 안 된다.
    """

    __tablename__ = "moves"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    slug: Mapped[str] = mapped_column(Text, nullable=False)
    name_ko: Mapped[str] = mapped_column(Text, nullable=False)

    type: Mapped[str] = mapped_column(String(16), nullable=False)
    category: Mapped[str] = mapped_column(String(16), nullable=False)
    # null = 고정 위력 없음(자이로볼·카운터처럼 상황에 따라 결정). 0 과 구분된다.
    power: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    # null = 명중 판정 없음
    accuracy: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    pp: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    priority: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)

    contact: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # 특성 보정 그룹 (slicing / punch / bite / bullet ...). 접촉은 별도 컬럼이다.
    flags: Mapped[list[str]] = mapped_column(JsonType, nullable=False, default=list)
    target: Mapped[str] = mapped_column(String(32), nullable=False)

    __table_args__ = (
        CheckConstraint("category IN ('physical', 'special', 'status')", name="ck_moves_category"),
        CheckConstraint("power IS NULL OR power > 0", name="ck_moves_power"),
        CheckConstraint("accuracy IS NULL OR accuracy BETWEEN 1 AND 100", name="ck_moves_accuracy"),
        CheckConstraint("category <> 'status' OR power IS NULL", name="ck_moves_status_no_power"),
        Index("ix_moves_name_ko", "name_ko"),
    )


class Ability(Base):
    """특성 마스터. 효과 데이터는 아직 없다 — 이름 매핑만 들어간다."""

    __tablename__ = "abilities"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name_en: Mapped[str] = mapped_column(Text, nullable=False)
    name_ko: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (Index("ix_abilities_name_ko", "name_ko"),)


class Item(Base):
    """도구 마스터. `data/items.json` 에서 적재한다.

    한글명은 아직 없다 (Phase 2-3c). 그때까지 `name_ko` 는 null 이다 —
    영문명을 대신 넣지 않는다 (ADR-005 fail-closed).
    """

    __tablename__ = "items"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name_en: Mapped[str] = mapped_column(Text, nullable=False)
    name_ko: Mapped[str | None] = mapped_column(Text, nullable=True)
    # DAMAGE_FLAT / TYPE_IMMUNITY / NONE ... 도메인의 ItemEffectKind 와 같은 값
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    multiplier: Mapped[Any | None] = mapped_column(Numeric(5, 3), nullable=True)
    # 어느 출처에서 온 수치인지 (champions-helper / mainline-assumed / verified)
    source: Mapped[str] = mapped_column(String(32), nullable=False)

    __table_args__ = (Index("ix_items_name_ko", "name_ko"),)


class Nature(Base):
    """성격 마스터. 상승/하락은 StatKey 문자열이다 (HP 는 보정을 받지 않는다)."""

    __tablename__ = "natures"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name_ko: Mapped[str] = mapped_column(Text, nullable=False)
    raised: Mapped[str | None] = mapped_column(String(16), nullable=True)
    lowered: Mapped[str | None] = mapped_column(String(16), nullable=True)

    __table_args__ = (
        # 보정은 항상 쌍이다. 하나만 있는 성격은 존재하지 않는다 (CLAUDE.md 5-1).
        CheckConstraint(
            "(raised IS NULL) = (lowered IS NULL)", name="ck_natures_modifier_is_paired"
        ),
        CheckConstraint("raised IS NULL OR raised <> lowered", name="ck_natures_differ"),
    )


class PokemonLearnset(Base):
    """어떤 포켓몬이 어떤 기술을 배우는가. 다대다 연결 테이블이다.

    ★ Phase 2-4 에서 "데이터 없음"을 이유로 뺐다가 되살린 테이블이다 ★

    그때 learnset 출처가 없다고 판단했는데 틀렸다 — championsbattledata 인덱스의
    `learnableMoveNames` 에 처음부터 있었다 (JOURNAL 2026-11-13).

    파티 빌더가 "배울 수 없는 기술"을 거르는 데 쓴다. 검증이 없으면 사용자가
    불가능한 파티를 저장하고, 그 파티로 계산한 수치를 근거라고 믿는다.
    """

    __tablename__ = "pokemon_learnset"

    pokemon_id: Mapped[str] = mapped_column(
        ForeignKey("pokemon.showdown_id", ondelete="CASCADE"), primary_key=True
    )
    move_id: Mapped[str] = mapped_column(
        ForeignKey("moves.id", ondelete="CASCADE"), primary_key=True
    )

    __table_args__ = (Index("ix_pokemon_learnset_move", "move_id"),)


class MetaSnapshot(Base):
    """수집 단위. **보존 기한이 있는 캐시다** (CLAUDE.md 8-2).

    `is_active` 가 서비스가 바라보는 스냅샷이고, 수집이 깨지면 이전 스냅샷으로
    되돌려 즉시 롤백한다. 활성 스냅샷은 (레귤레이션, 포맷)당 하나뿐이다.
    """

    __tablename__ = "meta_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    regulation_id: Mapped[int] = mapped_column(
        ForeignKey("regulations.id", ondelete="RESTRICT"), nullable=False
    )
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    format: Mapped[str] = mapped_column(String(16), nullable=False)
    collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # ★ 보존 기한. 지난 스냅샷은 정리 배치가 삭제한다 (Phase 2-6).
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    entries: Mapped[list[PokemonMeta]] = relationship(
        back_populates="snapshot", cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint(
            "regulation_id", "source", "format", "collected_at", name="uq_meta_snapshot"
        ),
        CheckConstraint("expires_at > collected_at", name="ck_snapshot_expiry_after_collection"),
        # (레귤레이션, 포맷)당 활성 스냅샷은 하나다. 부분 유니크 인덱스로 DB 가 보장한다 —
        # 애플리케이션 코드에 맡기면 전환 중 두 개가 활성인 순간이 생긴다.
        Index(
            "uq_meta_snapshot_active",
            "regulation_id",
            "format",
            unique=True,
            postgresql_where=text("is_active"),
        ),
    )


class PokemonMeta(Base):
    """스냅샷별 포켓몬 사용률. 채용률 상세는 `data` JSONB 에 담는다.

    출처마다 제공 형태가 달라서(개별 채용률 / 완성 샘플) 컬럼으로 고정하지 않는다.
    `raw` 는 수집 원본이다 — 파싱을 고쳤을 때 재수집 없이 재파싱할 수 있다.
    """

    __tablename__ = "pokemon_meta"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    snapshot_id: Mapped[int] = mapped_column(
        ForeignKey("meta_snapshots.id", ondelete="CASCADE"), nullable=False
    )
    pokemon_id: Mapped[str] = mapped_column(
        ForeignKey("pokemon.showdown_id", ondelete="RESTRICT"), nullable=False
    )
    rank: Mapped[int | None] = mapped_column(Integer, nullable=True)
    usage_rate: Mapped[Any | None] = mapped_column(Numeric(6, 3), nullable=True)
    data: Mapped[dict[str, Any]] = mapped_column(JsonType, nullable=False, default=dict)
    raw: Mapped[dict[str, Any] | None] = mapped_column(JsonType, nullable=True)

    snapshot: Mapped[MetaSnapshot] = relationship(back_populates="entries")

    __table_args__ = (
        UniqueConstraint("snapshot_id", "pokemon_id", name="uq_pokemon_meta"),
        Index("ix_pokemon_meta_pokemon", "pokemon_id"),
        Index("ix_pokemon_meta_data", "data", postgresql_using="gin"),
    )
