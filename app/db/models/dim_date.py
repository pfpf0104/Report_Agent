"""날짜 차원 테이블. YYYYMMDD 정수를 대리키로 사용해 fact 테이블 조인을 가볍게 한다."""
from __future__ import annotations

from sqlalchemy import Boolean, Date, SmallInteger
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class DimDate(Base):
    __tablename__ = "dim_date"

    date_id: Mapped[int] = mapped_column(primary_key=True)  # YYYYMMDD
    date: Mapped[object] = mapped_column(Date, nullable=False, unique=True)
    year: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    quarter: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    month: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    day: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    is_month_end: Mapped[bool] = mapped_column(Boolean, default=False)
