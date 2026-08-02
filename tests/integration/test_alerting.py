"""알림 이력(alert_log) — 텔레그램 전송 성공/실패와 무관하게 DB 기록은 항상 남아야 한다."""
import httpx
import pytest
import respx

from app.core.config import settings
from app.db.base import SessionLocal
from app.db.models.alert_log import AlertLog
from app.ingestion.alerting import send_alert

TELEGRAM_SEND_URL_PATTERN = "https://api.telegram.org/bot.*/sendMessage"


def _cleanup(session):
    session.query(AlertLog).filter(AlertLog.source == "_test_source").delete()
    session.commit()


@pytest.fixture()
def db():
    session = SessionLocal()
    _cleanup(session)
    yield session
    _cleanup(session)
    session.close()


@respx.mock
def test_send_alert_records_success_when_telegram_accepts(db, monkeypatch):
    monkeypatch.setattr(settings, "telegram_token", "test-token")
    monkeypatch.setattr(settings, "telegram_chat_id", "12345")
    respx.post("https://api.telegram.org/bottest-token/sendMessage").mock(
        return_value=httpx.Response(200, json={"ok": True})
    )

    entry = send_alert(db, category="job_failure", source="_test_source", severity="error", message="테스트 메시지")

    assert entry.telegram_sent is True
    stored = db.query(AlertLog).filter_by(id=entry.id).one()
    assert stored.message == "테스트 메시지"
    assert stored.severity == "error"


@respx.mock
def test_send_alert_still_logs_when_telegram_rejects(db, monkeypatch):
    """텔레그램이 4xx/5xx를 반환해도(잘못된 토큰 등) DB 기록은 반드시 남아야
    한다 — 알림 전송 실패가 기록 자체를 막으면 나중에 GET /ingestion/alerts로도
    무슨 일이 있었는지 알 수 없다."""
    monkeypatch.setattr(settings, "telegram_token", "bad-token")
    monkeypatch.setattr(settings, "telegram_chat_id", "12345")
    respx.post("https://api.telegram.org/botbad-token/sendMessage").mock(
        return_value=httpx.Response(401, json={"ok": False, "description": "Unauthorized"})
    )

    entry = send_alert(db, category="job_failure", source="_test_source", severity="error", message="테스트 메시지")

    assert entry.telegram_sent is False
    stored = db.query(AlertLog).filter_by(id=entry.id).one()
    assert stored.message == "테스트 메시지"


def test_send_alert_skips_telegram_when_not_configured(db, monkeypatch):
    """자격증명이 없으면(로컬 개발 등) 네트워크 호출 자체를 시도하지 않고
    DB 기록만 남겨야 한다 — R2/Supabase와 동일한 옵션형 연동 패턴."""
    monkeypatch.setattr(settings, "telegram_token", None)
    monkeypatch.setattr(settings, "telegram_chat_id", None)

    entry = send_alert(db, category="job_failure", source="_test_source", severity="error", message="테스트 메시지")

    assert entry.telegram_sent is False


def test_send_alert_truncates_overlong_messages(db, monkeypatch):
    monkeypatch.setattr(settings, "telegram_token", None)
    monkeypatch.setattr(settings, "telegram_chat_id", None)

    entry = send_alert(
        db, category="quality_gate", source="_test_source", severity="error", message="x" * 5000,
    )

    assert len(entry.message) == 4000
