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
    호출부의 본 작업을 막으면 안 된다).

    봇 토큰이 로그에 남지 않도록 두 겹으로 막는다:
      1) httpx/httpcore 로거를 이 함수 실행 중에만 WARNING 이상으로 올린다.
         httpx는 기본적으로 요청 URL을 INFO 레벨로 로깅하는데, 그 URL 자체가
         "https://api.telegram.org/bot<TOKEN>/sendMessage"라 애플리케이션
         로거 설정과 무관하게 토큰이 새어나간다(실측: caplog로 재현·확인함 —
         우리 쪽 예외 처리를 아무리 조심해도 httpx의 자체 요청 로그가 별도
         경로로 토큰을 남긴다).
      2) 우리가 직접 로깅하는 예외 메시지에서도 str(exc)/repr(exc)를 쓰지
         않는다 — HTTPStatusError/RequestError 둘 다 str()에 요청 URL을
         포함한다.
    """
    if not settings.telegram_token or not settings.telegram_chat_id:
        return False
    url = f"{TELEGRAM_API_BASE}/bot{settings.telegram_token}/sendMessage"

    httpx_logger = logging.getLogger("httpx")
    httpcore_logger = logging.getLogger("httpcore")
    prev_httpx_level = httpx_logger.level
    prev_httpcore_level = httpcore_logger.level
    httpx_logger.setLevel(logging.WARNING)
    httpcore_logger.setLevel(logging.WARNING)
    try:
        response = httpx.post(
            url, json={"chat_id": settings.telegram_chat_id, "text": message}, timeout=_TELEGRAM_TIMEOUT,
        )
        response.raise_for_status()
        return True
    except httpx.HTTPStatusError as exc:
        logger.error("텔레그램 알림 전송 실패: HTTP %s", exc.response.status_code)
        return False
    except httpx.RequestError as exc:
        # exc.request.url도 토큰을 담고 있어 str(exc)/repr(exc)를 그대로 찍지 않는다.
        logger.error("텔레그램 알림 전송 실패: %s", type(exc).__name__)
        return False
    except Exception as exc:
        # httpx.HTTPError 계열이 아닌 예상 밖 예외(직렬화 오류 등)까지 방어한다.
        # 여기서도 str(exc)/repr(exc)를 찍지 않는다 — 어떤 예외 타입이 url을
        # 담고 있을지 보장할 수 없으므로 타입명만 남긴다.
        logger.error("텔레그램 알림 전송 실패: 예상치 못한 오류(%s)", type(exc).__name__)
        return False
    finally:
        httpx_logger.setLevel(prev_httpx_level)
        httpcore_logger.setLevel(prev_httpcore_level)


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
