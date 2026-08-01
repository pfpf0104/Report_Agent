"""PDF에서 추출한 개별 숫자(데이터 포인트) + Cross-check 검증 상태.

검증 상태(verification_status):
  - unverified: 아직 어떤 체커도 시도하지 않았거나, 대조할 소스가 없어 판정 불가.
  - verified: 최소 1개 체커가 허용 오차 이내로 일치를 확인.
  - mismatch: 체커가 값을 찾았지만 허용 오차를 벗어남 — 사람이 반드시 봐야 할 값.
  - check_failed: 체커 자체가 실패(API 오류 등)해서 판정할 수 없었음 — unverified와
    구분해 "확인을 시도했지만 결과를 못 얻었다"는 걸 남긴다.
"""
from __future__ import annotations

from sqlalchemy import DateTime, ForeignKey, Numeric, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ExtractedValue(Base):
    __tablename__ = "extracted_value"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("extracted_document.id"), nullable=False)

    label: Mapped[str] = mapped_column(String(256), nullable=False)  # 원문에서 찾은 항목명(예: "자본총계")
    value: Mapped[float] = mapped_column(Numeric(24, 4), nullable=False)
    unit: Mapped[str | None] = mapped_column(String(32))  # 원/천원/백만원/% 등
    page_number: Mapped[int | None] = mapped_column()
    context_snippet: Mapped[str | None] = mapped_column(Text)  # 추출 근거가 된 원문 주변 텍스트(감사용)
    extraction_confidence: Mapped[float | None] = mapped_column(Numeric(4, 3))  # 파서 자체 신뢰도(0~1)

    verification_status: Mapped[str] = mapped_column(String(16), nullable=False, default="unverified")
    # 이 값을 검증하는 데 관여한 모든 체커의 결과를 감사 로그로 남긴다.
    # [{"checker": "dart_cross_check", "source": "internal", "matched_value": ..., "diff_pct": ...,
    #   "status": "verified", "checked_at": "..."}]
    verification_details: Mapped[list] = mapped_column(JSONB, default=list, server_default="[]")

    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), server_default=func.now())
