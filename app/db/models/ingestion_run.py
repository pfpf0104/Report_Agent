"""인제스천 잡 실행 이력. 성공/실패 상태와 오류 요약을 추적한다."""
from __future__ import annotations

from sqlalchemy import DateTime, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class IngestionRun(Base):
    __tablename__ = "ingestion_run"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String(32), nullable=False)  # dart/bok/fred/fmp
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="running")  # running/success/failed
    started_at: Mapped[object] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[object | None] = mapped_column(DateTime(timezone=True))
    error_summary: Mapped[str | None] = mapped_column(Text)
    raw_archive_path: Mapped[str | None] = mapped_column(String(256))
