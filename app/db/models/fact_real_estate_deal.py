"""상업용 부동산 실거래 팩트 테이블 (cap rate, 모기지 금리 등). RANGE 파티션 by deal_date."""
from __future__ import annotations

from sqlalchemy import BigInteger, Date, ForeignKey, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class FactRealEstateDeal(Base):
    __tablename__ = "fact_real_estate_deal"
    __table_args__ = ({"postgresql_partition_by": "RANGE (deal_date)"},)

    deal_id: Mapped[int] = mapped_column(BigInteger, autoincrement=True, primary_key=True)
    deal_date: Mapped[object] = mapped_column(Date, primary_key=True)
    # 이 실거래를 "알 수 있게 된" 날. 한국 실거래가 공개는 계약일로부터 통상
    # 30일 이상 지연되므로 deal_date를 그대로 쓰면 look-ahead bias가 생긴다.
    knowledge_date: Mapped[object] = mapped_column(Date, nullable=False, index=True)
    asset_id: Mapped[int] = mapped_column(ForeignKey("dim_asset.asset_id"), nullable=False)
    cap_rate: Mapped[float | None] = mapped_column(Numeric(6, 4))
    mortgage_rate: Mapped[float | None] = mapped_column(Numeric(6, 4))
    price_krw: Mapped[int | None] = mapped_column(BigInteger)
    area_sqm: Mapped[float | None] = mapped_column(Numeric(10, 2))
    deal_type: Mapped[str | None] = mapped_column(String(32))
    source: Mapped[str | None] = mapped_column(String(64))
