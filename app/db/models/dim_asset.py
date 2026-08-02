"""자산 차원 테이블.

주식(예: Alteogen, Almek, Korea PIM), 상업용 부동산(예: Pangyo Tech1,
Yeouido One Sentinel), 매크로 자산(ETF, 채권지수 등)을 하나의 테이블에서
asset_type으로 구분해 관리한다. 자산유형별로만 의미 있는 부가정보는
`attributes` JSONB에 담아 스키마 변경 없이 확장한다.
"""
from __future__ import annotations

import enum

from sqlalchemy import DateTime, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class AssetType(str, enum.Enum):
    EQUITY = "EQUITY"
    ETF = "ETF"
    REAL_ESTATE = "REAL_ESTATE"
    MACRO = "MACRO"  # 금리류. 단위 규약은 bp(G13 참고) — duration_controller의 yield_*_bp와 맞춘다.
    # 금리가 아닌 매크로 지표(스프레드·지수). MACRO와 단위가 다르다 — 스프레드는
    # %p로 음수 가능(금리곡선 역전), 지수(달러인덱스 등)는 100 안팎의 무차원
    # 수치라 bp로 정규화하면 의미가 없다. 두 asset_type을 분리해 각자 원단위
    # 그대로 저장하고, app/ingestion/quality.py의 상식범위도 asset_type별로 갈린다.
    MACRO_INDEX = "MACRO_INDEX"


class DimAsset(Base):
    __tablename__ = "dim_asset"

    asset_id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    # DB enum이 아니라 문자열로 저장한다. AssetType은 애플리케이션 레벨 검증용이며,
    # 새 자산유형이 늘어날 때 ALTER TYPE 없이 값만 추가하면 되도록 하기 위함이다.
    asset_type: Mapped[str] = mapped_column(String(16), nullable=False)
    code: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    name_kr: Mapped[str] = mapped_column(String(128), nullable=False)
    name_en: Mapped[str | None] = mapped_column(String(128))
    sector: Mapped[str | None] = mapped_column(String(64))
    country: Mapped[str | None] = mapped_column(String(8))
    currency: Mapped[str] = mapped_column(String(8), default="KRW", server_default="KRW")
    attributes: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), server_default=func.now())
