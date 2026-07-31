"""일별 시세 팩트 테이블 (RANGE 파티션 by trade_date).

PostgreSQL 파티션 테이블은 파티션 키(trade_date)가 모든 유니크/기본키에
포함되어야 하므로 PK를 (asset_id, trade_date) 복합키로 둔다. 실제 파티션
생성(연/월 단위)은 Alembic 마이그레이션에서 DDL로 처리한다 — SQLAlchemy
autogenerate는 파티션 자식 테이블까지 만들어주지 않기 때문이다.
"""
from __future__ import annotations

from sqlalchemy import BigInteger, Date, ForeignKey, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class FactMarketDaily(Base):
    __tablename__ = "fact_market_daily"
    __table_args__ = ({"postgresql_partition_by": "RANGE (trade_date)"},)

    asset_id: Mapped[int] = mapped_column(ForeignKey("dim_asset.asset_id"), primary_key=True)
    trade_date: Mapped[object] = mapped_column(Date, primary_key=True)
    open: Mapped[float | None] = mapped_column(Numeric(18, 4))
    high: Mapped[float | None] = mapped_column(Numeric(18, 4))
    low: Mapped[float | None] = mapped_column(Numeric(18, 4))
    close: Mapped[float | None] = mapped_column(Numeric(18, 4))
    adj_close: Mapped[float | None] = mapped_column(Numeric(18, 4))
    volume: Mapped[int | None] = mapped_column(BigInteger)
    source: Mapped[str | None] = mapped_column(String(32))
