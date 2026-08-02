"""인제스천 실패·품질게이트 알림 이력. 텔레그램 전송 성공 여부와 무관하게 기록한다.

텔레그램 자격증명이 없거나 API 호출이 실패해도 이 테이블에는 남아야 한다 —
"무슨 일이 있었는지"를 사람이 나중에 GET /ingestion/alerts로 조회할 수 있는
최소 보증이다(app/ingestion/alerting.py 참고).
"""
from __future__ import annotations

from sqlalchemy import Boolean, DateTime, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class AlertLog(Base):
    __tablename__ = "alert_log"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    category: Mapped[str] = mapped_column(String(32), nullable=False)  # job_failure/quality_gate
    source: Mapped[str] = mapped_column(String(64), nullable=False)  # job 이름 또는 "quality_gate"
    severity: Mapped[str] = mapped_column(String(16), nullable=False)  # error/warning
    message: Mapped[str] = mapped_column(Text, nullable=False)
    telegram_sent: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), server_default=func.now())
