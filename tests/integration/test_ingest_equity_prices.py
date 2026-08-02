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


def _mock_all_symbols(overrides: dict[str, httpx.Response] | None = None) -> None:
    """SYMBOLS의 모든 심볼에 기본 응답을 mock하고, overrides로 개별 심볼만 덮어쓴다.

    job.SYMBOLS는 CallRank의 11개 섹터 ETF + SPY(총 12개)라서 특정 심볼
    하나만 mock하면 나머지가 respx.AllMockedAssertionError로 실패한다.
    """
    overrides = overrides or {}
    for i, symbol in enumerate(job.SYMBOLS):
        response = overrides.get(symbol) or httpx.Response(
            200, json=_chart_response(symbol, 100.0 + i, 99.0 + i, 101.0 + i, 98.0 + i, 1000 + i)
        )
        respx.get(f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}").mock(return_value=response)


@respx.mock
def test_run_upserts_quotes_into_fact_market_daily(db):
    _mock_all_symbols(
        {
            "XLE": httpx.Response(200, json=_chart_response("XLE", 95.32, 94.0, 96.0, 93.5, 1000)),
            "SPY": httpx.Response(200, json=_chart_response("SPY", 550.1, 548.0, 551.0, 547.0, 2000)),
        }
    )

    job.run()

    xle = db.query(DimAsset).filter_by(code="XLE").one()
    row = db.query(FactMarketDaily).filter_by(asset_id=xle.asset_id).one()
    assert float(row.close) == 95.32
    assert row.source == "yahoo_finance"

    run_log = db.query(IngestionRun).filter_by(source="yahoo_equity_prices").order_by(IngestionRun.id.desc()).first()
    assert run_log.status == "success"


@respx.mock
def test_run_upserts_all_sector_etfs_and_benchmark(db):
    """핵심 회귀: SYMBOLS가 11개 섹터 ETF + SPY로 확장된 뒤, 실제로 전부
    적재되는지 확인한다 — CallRank 성과 페이지(risk/report_context.py)가
    리스크패리티 계산에 최소 2개 자산을 요구하므로, 섹터 ETF가 하나만
    적재되면 항상 보류 상태로 남는다."""
    _mock_all_symbols()

    job.run()

    for symbol in job.SYMBOLS:
        asset = db.query(DimAsset).filter_by(code=symbol).one()
        row = db.query(FactMarketDaily).filter_by(asset_id=asset.asset_id).one()
        assert row.source == "yahoo_finance"

    assert len(job.SYMBOLS) >= 11  # 섹터 11개 + SPY, 우연히 다시 2개로 줄지 않았는지 고정


@respx.mock
def test_run_records_failure_on_api_error(db):
    _mock_all_symbols(
        {"XLE": httpx.Response(200, json={"chart": {"result": None, "error": {"code": "Not Found"}}})}
    )

    with pytest.raises(Exception):
        job.run()

    run_log = db.query(IngestionRun).filter_by(source="yahoo_equity_prices").order_by(IngestionRun.id.desc()).first()
    assert run_log.status == "failed"
    assert run_log.error_summary
