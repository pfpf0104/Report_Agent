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
def test_send_alert_records_success_when_telegram_accepts(db, monkeypatch, caplog):
    import logging

    secret_token = "test-token-should-not-leak-on-success-either"
    monkeypatch.setattr(settings, "telegram_token", secret_token)
    monkeypatch.setattr(settings, "telegram_chat_id", "12345")
    respx.post(f"https://api.telegram.org/bot{secret_token}/sendMessage").mock(
        return_value=httpx.Response(200, json={"ok": True})
    )

    with caplog.at_level(logging.DEBUG):
        entry = send_alert(db, category="job_failure", source="_test_source", severity="error", message="테스트 메시지")

    assert entry.telegram_sent is True
    stored = db.query(AlertLog).filter_by(id=entry.id).one()
    assert stored.message == "테스트 메시지"
    assert stored.severity == "error"
    # 성공 응답이어도 httpx의 INFO 요청 로그에 토큰이 실린 URL이 찍힐 수 있다
    # (실측 재현: 실패 경로와 동일한 문제) — 성공 경로도 억제돼야 한다.
    assert secret_token not in caplog.text


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


@respx.mock
def test_bot_token_never_appears_in_logs_on_telegram_failure(db, monkeypatch, caplog):
    """httpx.HTTPStatusError.__str__()에는 요청 URL(=봇 토큰이 그대로 박힌
    https://api.telegram.org/bot<TOKEN>/sendMessage)이 포함된다. 이걸
    logger.exception()/logger.error("%s", exc)로 그대로 찍으면 서버 로그에
    토큰이 평문으로 남는다(로그 수집기로 나가면 그대로 유출). 실패 시
    로그 어디에도 토큰 문자열이 나타나면 안 된다."""
    import logging

    secret_token = "123456:REALTOKENVALUE_SHOULD_NEVER_APPEAR"
    monkeypatch.setattr(settings, "telegram_token", secret_token)
    monkeypatch.setattr(settings, "telegram_chat_id", "12345")
    respx.post(f"https://api.telegram.org/bot{secret_token}/sendMessage").mock(
        return_value=httpx.Response(401, json={"ok": False, "description": "Unauthorized"})
    )

    with caplog.at_level(logging.DEBUG):
        entry = send_alert(db, category="job_failure", source="_test_source", severity="error", message="테스트")

    assert entry.telegram_sent is False
    assert secret_token not in caplog.text


@respx.mock
def test_bot_token_never_appears_in_logs_on_network_error(db, monkeypatch, caplog):
    """HTTPStatusError뿐 아니라 RequestError(연결 실패 등) 경로에서도 exc.request.url
    에 토큰이 담겨 있다 — 이 경로도 str(exc)를 그대로 찍지 않는지 확인한다."""
    import logging

    secret_token = "123456:ANOTHER_SECRET_TOKEN_MUST_NOT_LEAK"
    monkeypatch.setattr(settings, "telegram_token", secret_token)
    monkeypatch.setattr(settings, "telegram_chat_id", "12345")
    respx.post(f"https://api.telegram.org/bot{secret_token}/sendMessage").mock(
        side_effect=httpx.ConnectError("connection refused")
    )

    with caplog.at_level(logging.DEBUG):
        entry = send_alert(db, category="job_failure", source="_test_source", severity="error", message="테스트")

    assert entry.telegram_sent is False
    assert secret_token not in caplog.text
