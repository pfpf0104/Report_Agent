"""분기 재무제표 팩트 테이블. 거래량이 적어 파티션 없이 일반 테이블로 둔다."""
from __future__ import annotations

from sqlalchemy import BigInteger, ForeignKey, Numeric, SmallInteger, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class FactFinancialQuarterly(Base):
    __tablename__ = "fact_financial_quarterly"
    __table_args__ = (
        UniqueConstraint("asset_id", "fiscal_year", "fiscal_quarter", name="uq_financial_quarter"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    asset_id: Mapped[int] = mapped_column(ForeignKey("dim_asset.asset_id"), nullable=False)
    fiscal_year: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    fiscal_quarter: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    revenue: Mapped[int | None] = mapped_column(BigInteger)
    operating_income: Mapped[int | None] = mapped_column(BigInteger)
    net_income: Mapped[int | None] = mapped_column(BigInteger)
    eps: Mapped[float | None] = mapped_column(Numeric(12, 2))
    bps: Mapped[float | None] = mapped_column(Numeric(12, 2))
    roe: Mapped[float | None] = mapped_column(Numeric(6, 4))
    source: Mapped[str | None] = mapped_column(String(64))
