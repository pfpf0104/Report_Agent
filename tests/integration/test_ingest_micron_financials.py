"""네트워크는 respx로 mock하지만 실제 로컬 Postgres에 쓰는 통합 테스트."""
from datetime import date, timedelta

import httpx
import pytest
import respx

import app.ingestion.connectors.fmp_client as fmp_client
import app.ingestion.jobs.ingest_micron_financials as job
from app.db.base import SessionLocal
from app.db.models.dim_asset import DimAsset
from app.db.models.fact_financial_quarterly import FactFinancialQuarterly
from app.db.models.ingestion_run import IngestionRun

KEY_METRICS_URL = "https://financialmodelingprep.com/stable/key-metrics"


@pytest.fixture(autouse=True)
def _setup(monkeypatch):
    monkeypatch.setattr(fmp_client.settings, "fmp_api_key", "test-key")


def _cleanup(session):
    session.query(FactFinancialQuarterly).filter(FactFinancialQuarterly.asset_id.in_(
        session.query(DimAsset.asset_id).filter(DimAsset.code == job.MICRON_SYMBOL)
    )).delete(synchronize_session=False)
    session.query(DimAsset).filter(DimAsset.code == job.MICRON_SYMBOL).delete(synchronize_session=False)
    session.query(IngestionRun).filter_by(source="fmp_micron_financials").delete()
    session.commit()


@pytest.fixture()
def db():
    session = SessionLocal()
    _cleanup(session)
    yield session
    _cleanup(session)
    session.close()


@respx.mock
def test_run_upserts_quarterly_bps_and_roe(db):
    respx.get(KEY_METRICS_URL, params={"symbol": "MU", "period": "quarter", "limit": 8}).mock(
        return_value=httpx.Response(
            200,
            json=[
                {"symbol": "MU", "fiscalYear": 2026, "period": "Q3", "date": "2026-05-28",
                 "returnOnEquity": 0.18, "bookValuePerShare": 45.2},
                {"symbol": "MU", "fiscalYear": 2026, "period": "Q2", "date": "2026-02-26",
                 "returnOnEquity": 0.15, "bookValuePerShare": 44.1},
            ],
        )
    )

    job.run()

    asset = db.query(DimAsset).filter_by(code="MU").one()
    rows = (
        db.query(FactFinancialQuarterly)
        .filter_by(asset_id=asset.asset_id)
        .order_by(FactFinancialQuarterly.fiscal_quarter)
        .all()
    )
    assert len(rows) == 2

    q2 = rows[0]
    assert q2.fiscal_quarter == 2
    assert float(q2.bps) == pytest.approx(44.1)
    assert float(q2.roe) == pytest.approx(0.15)
    assert q2.source == "fmp"
    assert q2.knowledge_date == date(2026, 2, 26) + timedelta(days=job.FILING_LAG_DAYS)

    run_log = db.query(IngestionRun).filter_by(source="fmp_micron_financials").order_by(IngestionRun.id.desc()).first()
    assert run_log.status == "success"


@respx.mock
def test_run_is_idempotent_on_rerun(db):
    """같은 분기를 다시 적재해도 중복 행이 생기지 않고 값만 갱신된다."""
    respx.get(KEY_METRICS_URL, params={"symbol": "MU", "period": "quarter", "limit": 8}).mock(
        return_value=httpx.Response(
            200,
            json=[{"symbol": "MU", "fiscalYear": 2026, "period": "Q2", "date": "2026-02-26",
                   "returnOnEquity": 0.15, "bookValuePerShare": 44.1}],
        )
    )
    job.run()
    job.run()

    asset = db.query(DimAsset).filter_by(code="MU").one()
    assert db.query(FactFinancialQuarterly).filter_by(asset_id=asset.asset_id).count() == 1


@respx.mock
def test_run_skips_rows_missing_required_fields(db):
    respx.get(KEY_METRICS_URL, params={"symbol": "MU", "period": "quarter", "limit": 8}).mock(
        return_value=httpx.Response(
            200,
            json=[
                {"symbol": "MU", "fiscalYear": None, "period": "Q2", "date": "2026-02-26",
                 "returnOnEquity": 0.15, "bookValuePerShare": 44.1},
                {"symbol": "MU", "fiscalYear": 2026, "period": "FY", "date": "2026-02-26",
                 "returnOnEquity": 0.15, "bookValuePerShare": 44.1},
            ],
        )
    )
    job.run()

    assert db.query(FactFinancialQuarterly).count() == 0


@respx.mock
def test_run_propagates_api_error_and_marks_run_failed(db):
    respx.get(KEY_METRICS_URL, params={"symbol": "MU", "period": "quarter", "limit": 8}).mock(
        return_value=httpx.Response(200, json=[])
    )

    with pytest.raises(fmp_client.FmpApiError):
        job.run()

    run_log = db.query(IngestionRun).filter_by(source="fmp_micron_financials").order_by(IngestionRun.id.desc()).first()
    assert run_log.status == "failed"
