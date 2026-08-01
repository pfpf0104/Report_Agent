"""네트워크는 respx로 mock하지만 실제 로컬 Postgres에 쓰는 통합 테스트."""
import httpx
import pytest
import respx

import app.ingestion.jobs.ingest_equity_prices as job
from app.db.base import SessionLocal
from app.db.models.dim_asset import DimAsset
from app.db.models.fact_market_daily import FactMarketDaily
from app.db.models.ingestion_run import IngestionRun


@pytest.fixture()
def db():
    session = SessionLocal()
    yield session
    session.query(FactMarketDaily).filter(FactMarketDaily.asset_id.in_(
        session.query(DimAsset.asset_id).filter(DimAsset.code.in_(job.SYMBOLS))
    )).delete(synchronize_session=False)
    session.query(DimAsset).filter(DimAsset.code.in_(job.SYMBOLS)).delete(synchronize_session=False)
    session.query(IngestionRun).filter_by(source="yahoo_equity_prices").delete()
    session.commit()
    session.close()


def _chart_response(symbol: str, close: float, open_: float, high: float, low: float, volume: int) -> dict:
    return {
        "chart": {
            "result": [
                {
                    "meta": {"symbol": symbol, "currency": "USD"},
                    "timestamp": [1785504600],
                    "indicators": {
                        "quote": [
                            {
                                "open": [open_],
                                "high": [high],
                                "low": [low],
                                "close": [close],
                                "volume": [volume],
                            }
                        ]
                    },
                }
            ],
            "error": None,
        }
    }


@respx.mock
def test_run_upserts_quotes_into_fact_market_daily(db):
    respx.get("https://query1.finance.yahoo.com/v8/finance/chart/XLE").mock(
        return_value=httpx.Response(200, json=_chart_response("XLE", 95.32, 94.0, 96.0, 93.5, 1000))
    )
    respx.get("https://query1.finance.yahoo.com/v8/finance/chart/SPY").mock(
        return_value=httpx.Response(200, json=_chart_response("SPY", 550.1, 548.0, 551.0, 547.0, 2000))
    )

    job.run()

    xle = db.query(DimAsset).filter_by(code="XLE").one()
    row = db.query(FactMarketDaily).filter_by(asset_id=xle.asset_id).one()
    assert float(row.close) == 95.32
    assert row.source == "yahoo_finance"

    run_log = db.query(IngestionRun).filter_by(source="yahoo_equity_prices").order_by(IngestionRun.id.desc()).first()
    assert run_log.status == "success"


@respx.mock
def test_run_records_failure_on_api_error(db):
    respx.get("https://query1.finance.yahoo.com/v8/finance/chart/XLE").mock(
        return_value=httpx.Response(200, json={"chart": {"result": None, "error": {"code": "Not Found"}}})
    )
    respx.get("https://query1.finance.yahoo.com/v8/finance/chart/SPY").mock(
        return_value=httpx.Response(200, json=_chart_response("SPY", 550.1, 548.0, 551.0, 547.0, 2000))
    )

    with pytest.raises(Exception):
        job.run()

    run_log = db.query(IngestionRun).filter_by(source="yahoo_equity_prices").order_by(IngestionRun.id.desc()).first()
    assert run_log.status == "failed"
    assert run_log.error_summary
