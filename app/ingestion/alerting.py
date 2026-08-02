"""인제스천 실패·품질게이트 오류를 텔레그램으로 알리고 alert_log에 남긴다.

DB 기록이 항상 우선이다 — 텔레그램 자격증명이 없거나(settings.telegram_token
None) 전송 자체가 실패해도(네트워크 문제, 잘못된 chat_id 등) alert_log에는
반드시 남는다. 그래야 텔레그램을 놓쳐도 GET /ingestion/alerts로 나중에
확인할 수 있다 — "알림을 보내려 했다"와 "알림이 실제로 갔다"를 구분한다.

이 모듈은 알림 판단을 하지 않는다. 언제 부를지는 호출부(scheduler.py,
품질게이트 트리거)가 정한다.
"""
from __future__ import annotations

import logging

import httpx
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models.alert_log import AlertLog

logger = logging.getLogger("app.ingestion.alerting")

TELEGRAM_API_BASE = "https://api.telegram.org"
_TELEGRAM_TIMEOUT = 10.0


def _send_telegram(message: str) -> bool:
    """전송 성공 여부만 반환한다 — 실패해도 예외를 던지지 않는다(알림 실패로
    호출부의 본 작업을 막으면 안 된다)."""
    if not settings.telegram_token or not settings.telegram_chat_id:
        return False
    try:
        response = httpx.post(
            f"{TELEGRAM_API_BASE}/bot{settings.telegram_token}/sendMessage",
            json={"chat_id": settings.telegram_chat_id, "text": message},
            timeout=_TELEGRAM_TIMEOUT,
        )
        response.raise_for_status()
        return True
    except Exception:
        logger.exception("텔레그램 알림 전송 실패")
        return False


def send_alert(db: Session, category: str, source: str, severity: str, message: str) -> AlertLog:
    """alert_log에 기록하고 텔레그램 전송을 시도한다. 항상 AlertLog를 반환한다."""
    sent = _send_telegram(f"[{severity.upper()}] {source}\n{message}")
    entry = AlertLog(
        category=category, source=source, severity=severity, message=message[:4000], telegram_sent=sent,
    )
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return entry
