"""네트워크는 respx로 mock하지만 실제 로컬 Postgres에 쓰는 통합 테스트."""
from datetime import date

import httpx
import pytest
import respx

import app.ingestion.jobs.backfill_korean_equity_prices as job
from app.db.base import SessionLocal
from app.db.models.dim_asset import DimAsset
from app.db.models.fact_financial_quarterly import FactFinancialQuarterly
from app.db.models.fact_market_daily import FactMarketDaily
from app.db.models.ingestion_run import IngestionRun


def _cleanup(session, codes):
    # 005930/000660은 fact_market_daily와 fact_financial_quarterly 양쪽에서
    # 참조될 수 있는 자산이다(다른 테스트 파일/실제 운영 데이터) — dim_asset을
    # 지우기 전에 두 fact 테이블 다 정리해야 FK 위반이 안 난다.
    session.query(FactMarketDaily).filter(FactMarketDaily.asset_id.in_(
        session.query(DimAsset.asset_id).filter(DimAsset.code.in_(codes))
    )).delete(synchronize_session=False)
    session.query(FactFinancialQuarterly).filter(FactFinancialQuarterly.asset_id.in_(
        session.query(DimAsset.asset_id).filter(DimAsset.code.in_(codes))
    )).delete(synchronize_session=False)
    session.query(DimAsset).filter(DimAsset.code.in_(codes)).delete(synchronize_session=False)
    session.query(IngestionRun).filter_by(source="backfill_korean_equity_prices").delete()
    session.commit()


@pytest.fixture()
def db():
    from app.ingestion.jobs.ingest_korean_equity_prices import SYMBOLS

    session = SessionLocal()
    codes = list(SYMBOLS.keys())
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
                    "meta": {"currency": "KRW"},
                    "timestamp": timestamps,
                    "indicators": {
                        "quote": [
                            {"open": closes, "high": closes, "low": closes, "close": closes, "volume": [1000] * n}
                        ]
                    },
                }
            ],
            "error": None,
        }
    }


@respx.mock
def test_backfill_uses_ks_suffix_and_sets_krw_currency(db):
    ts = 1768435200  # 2026-01-15 UTC
    respx.get("https://query1.finance.yahoo.com/v8/finance/chart/005930.KS").mock(
        return_value=httpx.Response(200, json=_chart_response([ts], [70000.0]))
    )
    respx.get("https://query1.finance.yahoo.com/v8/finance/chart/000660.KS").mock(
        return_value=httpx.Response(200, json=_chart_response([ts], [170000.0]))
    )

    job.run()

    samsung = db.query(DimAsset).filter_by(code="005930").one()
    assert samsung.currency == "KRW"
    assert samsung.name_kr == "삼성전자"

    row = db.query(FactMarketDaily).filter_by(asset_id=samsung.asset_id).one()
    assert row.trade_date == date(2026, 1, 15)
    assert row.knowledge_date == date(2026, 1, 15)
    assert float(row.close) == 70000.0
    assert row.source == "yahoo_finance_backfill"


@respx.mock
def test_backfill_coexists_with_kis_seeded_row_on_different_date(db):
    """KIS가 넣은 최신 거래일 행은 그대로 두고, 백필은 다른 날짜만 채운다."""
    samsung = DimAsset(asset_type="EQUITY", code="005930", name_kr="삼성전자", currency="KRW")
    db.add(samsung)
    db.commit()
    db.refresh(samsung)
    db.add(
        FactMarketDaily(
            asset_id=samsung.asset_id, trade_date=date(2026, 8, 1), knowledge_date=date(2026, 8, 1),
            close=262500.0, adj_close=262500.0, source="kis",
        )
    )
    db.commit()

    ts = 1768435200  # 2026-01-15, KIS가 넣은 날짜와 다름
    respx.get("https://query1.finance.yahoo.com/v8/finance/chart/005930.KS").mock(
        return_value=httpx.Response(200, json=_chart_response([ts], [70000.0]))
    )
    respx.get("https://query1.finance.yahoo.com/v8/finance/chart/000660.KS").mock(
        return_value=httpx.Response(200, json=_chart_response([ts], [170000.0]))
    )

    job.run()

    rows = db.query(FactMarketDaily).filter_by(asset_id=samsung.asset_id).order_by(FactMarketDaily.trade_date).all()
    assert len(rows) == 2
    assert rows[0].source == "yahoo_finance_backfill"
    assert rows[1].source == "kis"
