"""분기 재무제표 팩트 테이블. 거래량이 적어 파티션 없이 일반 테이블로 둔다."""
from __future__ import annotations

from sqlalchemy import BigInteger, Date, ForeignKey, Numeric, SmallInteger, String, UniqueConstraint
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
    # 이 재무제표를 "알 수 있게 된" 날(공시일). 회계연도 말과 실제 공시 사이에는
    # 통상 수개월 간극이 있어(사업보고서 법정 제출기한 90일) 이 구분이 특히 중요하다 —
    # 없으면 아직 공시되지 않은 실적으로 과거를 평가하는 look-ahead bias가 생긴다.
    knowledge_date: Mapped[object] = mapped_column(Date, nullable=False, index=True)
    revenue: Mapped[int | None] = mapped_column(BigInteger)
    operating_income: Mapped[int | None] = mapped_column(BigInteger)
    net_income: Mapped[int | None] = mapped_column(BigInteger)
    eps: Mapped[float | None] = mapped_column(Numeric(12, 2))
    bps: Mapped[float | None] = mapped_column(Numeric(12, 2))
    roe: Mapped[float | None] = mapped_column(Numeric(6, 4))
    source: Mapped[str | None] = mapped_column(String(64))
