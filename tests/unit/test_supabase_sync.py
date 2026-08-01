import json
from dataclasses import dataclass
from datetime import date

import httpx
import pytest

from app.sync import supabase_sync


@pytest.fixture(autouse=True)
def _configure_supabase(monkeypatch):
    monkeypatch.setattr(supabase_sync.settings, "supabase_url", "https://example.supabase.co")
    monkeypatch.setattr(supabase_sync.settings, "supabase_service_key", "test-service-key")
    yield


@dataclass(frozen=True)
class _Unserializable:
    name: str


def test_sync_report_snapshot_skips_silently_without_config(monkeypatch):
    monkeypatch.setattr(supabase_sync.settings, "supabase_url", None)
    monkeypatch.setattr(supabase_sync.settings, "supabase_service_key", None)

    def _fail_post(*args, **kwargs):
        raise AssertionError("설정 안 됐으면 httpx.post가 호출되면 안 된다")

    monkeypatch.setattr(httpx, "post", _fail_post)
    supabase_sync.sync_report_snapshot(report_type="valuation", report_date=date(2026, 7, 30), context={})


def test_sync_report_snapshot_handles_non_json_serializable_context(monkeypatch):
    """실제로 build_valuation_context()의 RimScenario(dataclass) 필드가 이 경로에서
    httpx의 기본 json= 직렬화를 깨뜨리는 것을 재현·회귀 확인한다."""
    captured = {}

    class _FakeResponse:
        def raise_for_status(self):
            pass

    def _fake_post(url, headers=None, content=None, timeout=None, **kwargs):
        captured["content"] = content
        return _FakeResponse()

    monkeypatch.setattr(httpx, "post", _fake_post)

    context = {"samsung": {"base_scenario": _Unserializable(name="점진적 추격")}}
    supabase_sync.sync_report_snapshot(report_type="valuation", report_date=date(2026, 7, 30), context=context)

    body = json.loads(captured["content"])
    assert "_Unserializable" in body["context"]["samsung"]["base_scenario"]


def test_sync_report_snapshot_logs_warning_on_http_error(monkeypatch, caplog):
    def _fake_post(*args, **kwargs):
        request = httpx.Request("POST", "https://example.supabase.co/rest/v1/report_snapshot")
        raise httpx.ConnectError("connection refused", request=request)

    monkeypatch.setattr(httpx, "post", _fake_post)

    with caplog.at_level("WARNING"):
        supabase_sync.sync_report_snapshot(report_type="valuation", report_date=date(2026, 7, 30), context={})

    assert "동기화 실패" in caplog.text
