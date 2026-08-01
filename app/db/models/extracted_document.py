"""PDF 자산화 대상 원본 문서. 업로드된 PDF 1건당 1행."""
from __future__ import annotations

from sqlalchemy import DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ExtractedDocument(Base):
    __tablename__ = "extracted_document"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    filename: Mapped[str] = mapped_column(String(256), nullable=False)
    file_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)  # sha256, 중복 업로드 방지
    storage_path: Mapped[str] = mapped_column(String(512), nullable=False)
    page_count: Mapped[int | None] = mapped_column(Integer)
    # text/ocr/mixed — 페이지별로 텍스트 추출이 되면 text, 안 돼서 OCR로 폴백했으면 ocr,
    # 문서 안에 두 경로가 섞여 있으면 mixed (extraction_method 참고).
    extraction_method: Mapped[str] = mapped_column(String(16), nullable=False, default="text")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="processing")  # processing/done/failed
    error_summary: Mapped[str | None] = mapped_column(Text)
    uploaded_at: Mapped[object] = mapped_column(DateTime(timezone=True), server_default=func.now())
    processed_at: Mapped[object | None] = mapped_column(DateTime(timezone=True))
