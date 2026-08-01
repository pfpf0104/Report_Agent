"""ingestion_router.py의 트리거 대상 job 등록을 확인한다.

app.main을 통째로 임포트하면 weasyprint(reports_router 경유)가 딸려오는데,
Windows에 GTK 네이티브 라이브러리가 없으면 임포트 자체가 실패한다 —
ingestion_router만 별도 FastAPI 앱에 마운트해 독립적으로 테스트한다.
"""
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routers import ingestion_router

app = FastAPI()
app.include_router(ingestion_router.router, prefix="/ingestion")
client = TestClient(app)


def test_all_backfill_jobs_are_registered():
    """핵심 회귀: Phase 0-2 백필 job 4개는 매일 도는 스케줄러 대상이 아니라
    재해복구/신규환경 셋업 시에만 필요하므로, 수동 트리거 엔드포인트를 통해서만
    재실행 가능해야 한다. 여기 등록이 빠지면 콘솔에서 직접 파이썬을 실행하는
    것 외에는 재실행할 방법이 없어진다."""
    expected = {
        "backfill_macro_rates",
        "backfill_equity_prices",
        "backfill_korean_equity_prices",
        "backfill_financial_statements",
    }
    assert expected <= set(ingestion_router._JOBS.keys())


def test_daily_jobs_still_registered():
    expected = {"equity_prices", "korean_equity_prices", "macro_rates", "financial_statements", "real_estate_deals"}
    assert expected <= set(ingestion_router._JOBS.keys())


def test_trigger_known_backfill_job_returns_triggered():
    with patch.object(ingestion_router.backfill_macro_rates, "run") as mock_run:
        response = client.post("/ingestion/trigger/backfill_macro_rates")

    assert response.status_code == 200
    assert response.json() == {"status": "triggered", "job": "backfill_macro_rates"}
    # 이 assert가 없으면 테스트가 거짓으로 통과한다. _JOBS가 임포트 시점에 함수
    # 객체를 고정해 두면 패치가 무시되고 **진짜 job이 실행된다** — 유닛 테스트가
    # BOK ECOS를 실제로 호출하고 DB에 쓰던 상태를 이 assert로 고정한다.
    mock_run.assert_called_once()


def test_trigger_unknown_job_returns_error_with_known_jobs_list():
    response = client.post("/ingestion/trigger/not_a_real_job")

    assert response.status_code == 200
    body = response.json()
    assert body["error"] == "unknown job: not_a_real_job"
    assert "backfill_financial_statements" in body["known_jobs"]
