"""네트워크는 respx로 mock하지만 실제 로컬 Postgres에 쓰는 통합 테스트."""
import httpx
import pytest
import respx

import app.ingestion.jobs.ingest_equity_prices as job
from app.db.base import SessionLocal
from app.db.models.dim_asset import DimAsset
from app.db.models.fact_market_daily import FactMarketDaily
from app.db.models.ingestion_run import IngestionRun


@pytest.fixture(autouse=True)
def _set_api_key(monkeypatch):
    import app.ingestion.connectors.fmp_client as fmp_client

    monkeypatch.setattr(fmp_client.settings, "fmp_api_key", "test-key")


@pytest.fixture()
def db():
    session = SessionLocal()
    yield session
    session.query(FactMarketDaily).filter(FactMarketDaily.asset_id.in_(
        session.query(DimAsset.asset_id).filter(DimAsset.code.in_(job.SYMBOLS))
    )).delete(synchronize_session=False)
    session.query(DimAsset).filter(DimAsset.code.in_(job.SYMBOLS)).delete(synchronize_session=False)
    session.query(IngestionRun).filter_by(source="fmp_equity_prices").delete()
    session.commit()
    session.close()


@respx.mock
def test_run_upserts_quotes_into_fact_market_daily(db):
    respx.get("https://financialmodelingprep.com/api/v3/quote/XLE").mock(
        return_value=httpx.Response(200, json=[{"symbol": "XLE", "price": 95.32, "open": 94.0, "dayHigh": 96.0, "dayLow": 93.5, "volume": 1000}])
    )
    respx.get("https://financialmodelingprep.com/api/v3/quote/SPY").mock(
        return_value=httpx.Response(200, json=[{"symbol": "SPY", "price": 550.1, "open": 548.0, "dayHigh": 551.0, "dayLow": 547.0, "volume": 2000}])
    )

    job.run()

    xle = db.query(DimAsset).filter_by(code="XLE").one()
    row = db.query(FactMarketDaily).filter_by(asset_id=xle.asset_id).one()
    assert float(row.close) == 95.32
    assert row.source == "fmp"

    run_log = db.query(IngestionRun).filter_by(source="fmp_equity_prices").order_by(IngestionRun.id.desc()).first()
    assert run_log.status == "success"


@respx.mock
def test_run_records_failure_on_api_error(db):
    respx.get("https://financialmodelingprep.com/api/v3/quote/XLE").mock(return_value=httpx.Response(200, json=[]))
    respx.get("https://financialmodelingprep.com/api/v3/quote/SPY").mock(
        return_value=httpx.Response(200, json=[{"symbol": "SPY", "price": 550.1}])
    )

    with pytest.raises(Exception):
        job.run()

    run_log = db.query(IngestionRun).filter_by(source="fmp_equity_prices").order_by(IngestionRun.id.desc()).first()
    assert run_log.status == "failed"
    assert run_log.error_summary
