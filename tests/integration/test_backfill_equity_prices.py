"""네트워크는 respx로 mock하지만 실제 로컬 Postgres에 쓰는 통합 테스트."""
from datetime import date

import httpx
import pytest
import respx

import app.ingestion.jobs.backfill_equity_prices as job
from app.db.base import SessionLocal
from app.db.models.dim_asset import DimAsset
from app.db.models.fact_market_daily import FactMarketDaily
from app.db.models.ingestion_run import IngestionRun


def _cleanup(session, codes):
    session.query(FactMarketDaily).filter(FactMarketDaily.asset_id.in_(
        session.query(DimAsset.asset_id).filter(DimAsset.code.in_(codes))
    )).delete(synchronize_session=False)
    session.query(DimAsset).filter(DimAsset.code.in_(codes)).delete(synchronize_session=False)
    session.query(IngestionRun).filter_by(source="backfill_equity_prices").delete()
    session.commit()


@pytest.fixture()
def db():
    session = SessionLocal()
    codes = list(job.SYMBOLS)
    _cleanup(session, codes)
    yield session
    _cleanup(session, codes)
    session.close()


def _chart_response(timestamps: list[int], closes: list[float]) -> dict:
    n = len(timestamps)
    return {
        "chart": {
            "result": [
                {
                    "meta": {"currency": "USD"},
                    "timestamp": timestamps,
                    "indicators": {
                        "quote": [
                            {
                                "open": closes,
                                "high": closes,
                                "low": closes,
                                "close": closes,
                                "volume": [1000] * n,
                            }
                        ]
                    },
                }
            ],
            "error": None,
        }
    }


def _mock_all_symbols(ts: int, overrides: dict[str, float] | None = None) -> None:
    """job.SYMBOLS(11개 섹터 ETF + SPY)의 모든 심볼에 응답을 mock한다."""
    overrides = overrides or {}
    for i, symbol in enumerate(job.SYMBOLS):
        close = overrides.get(symbol, 100.0 + i)
        respx.get(f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}").mock(
            return_value=httpx.Response(200, json=_chart_response([ts], [close]))
        )


@respx.mock
def test_backfill_inserts_rows_with_trade_date_from_timestamp(db):
    # 2026-01-15 00:00:00 UTC
    ts = 1768435200
    _mock_all_symbols(ts, {"XLE": 95.5, "SPY": 550.0})

    job.run()

    xle = db.query(DimAsset).filter_by(code="XLE").one()
    row = db.query(FactMarketDaily).filter_by(asset_id=xle.asset_id).one()
    assert row.trade_date == date(2026, 1, 15)
    assert row.knowledge_date == date(2026, 1, 15)
    assert float(row.close) == 95.5
    assert row.source == "yahoo_finance_backfill"


@respx.mock
def test_backfill_inserts_all_sector_etfs_and_benchmark(db):
    """핵심 회귀: 11개 섹터 ETF + SPY 전부 백필돼야 한다 — CallRank 성과
    페이지가 리스크패리티 계산에 최소 2개 자산을 요구하므로, 섹터 ETF가
    하나만 채워지면 항상 보류 상태로 남는다(실제로 XLE만 있을 때 이 문제가
    재현됐다)."""
    ts = 1768435200
    _mock_all_symbols(ts)

    job.run()

    for symbol in job.SYMBOLS:
        asset = db.query(DimAsset).filter_by(code=symbol).one()
        row = db.query(FactMarketDaily).filter_by(asset_id=asset.asset_id).one()
        assert row.source == "yahoo_finance_backfill"

    assert len(job.SYMBOLS) >= 11


@respx.mock
def test_backfill_skips_dates_already_present(db):
    xle = DimAsset(asset_type="ETF", code="XLE", name_kr="XLE", currency="USD")
    db.add(xle)
    db.commit()
    db.refresh(xle)
    db.add(
        FactMarketDaily(
            asset_id=xle.asset_id, trade_date=date(2026, 1, 15), knowledge_date=date(2026, 1, 15),
            close=999.0, adj_close=999.0, source="manual_seed",
        )
    )
    db.commit()

    ts = 1768435200
    _mock_all_symbols(ts, {"XLE": 95.5, "SPY": 550.0})

    job.run()

    row = db.query(FactMarketDaily).filter_by(asset_id=xle.asset_id, trade_date=date(2026, 1, 15)).one()
    assert float(row.close) == 999.0
    assert row.source == "manual_seed"
