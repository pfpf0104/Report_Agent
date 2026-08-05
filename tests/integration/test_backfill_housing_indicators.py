"""네트워크는 respx로 mock하지만 실제 로컬 Postgres에 쓰는 통합 테스트."""
import httpx
import pytest
import respx

import app.ingestion.jobs.backfill_housing_indicators as job
from app.db.base import SessionLocal
from app.db.models.dim_asset import DimAsset
from app.db.models.fact_market_daily import FactMarketDaily
from app.db.models.ingestion_run import IngestionRun
from app.ingestion.jobs.ingest_housing_indicators import ALL_SERIES

FRED_URL = "https://api.stlouisfed.org/fred/series/observations"


@pytest.fixture(autouse=True)
def _set_api_key(monkeypatch):
    import app.ingestion.connectors.fred_client as fred_client

    monkeypatch.setattr(fred_client.settings, "fred_api_key", "test-key")


def _cleanup(session, codes):
    session.query(FactMarketDaily).filter(FactMarketDaily.asset_id.in_(
        session.query(DimAsset.asset_id).filter(DimAsset.code.in_(codes))
    )).delete(synchronize_session=False)
    session.query(DimAsset).filter(DimAsset.code.in_(codes)).delete(synchronize_session=False)
    session.query(IngestionRun).filter_by(source="backfill_housing_indicators").delete()
    session.commit()


@pytest.fixture()
def db():
    session = SessionLocal()
    codes = list(ALL_SERIES.keys())
    _cleanup(session, codes)
    yield session
    _cleanup(session, codes)
    session.close()


def _vintage_response(rows: list[tuple[str, str, str]]) -> dict:
    return {
        "observations": [
            {"realtime_start": rs, "realtime_end": "9999-12-31", "date": d, "value": v}
            for rs, d, v in rows
        ]
    }


def _mock_all_series(rows: list[tuple[str, str, str]] | None = None) -> None:
    default_rows = rows or [
        ("2021-03-25", "2021-01-31", "260.0"),
        ("2026-03-25", "2026-01-31", "312.5"),
    ]
    for series_id in ALL_SERIES.values():
        respx.get(FRED_URL, params={"series_id": series_id}).mock(
            return_value=httpx.Response(200, json=_vintage_response(default_rows))
        )


@respx.mock
def test_backfill_inserts_multiple_months_with_correct_knowledge_date(db):
    _mock_all_series([
        ("2021-03-25", "2021-01-31", "260.0"),
        ("2026-03-25", "2026-01-31", "312.5"),
    ])

    job.run()

    asset = db.query(DimAsset).filter_by(code="USHPINAT").one()
    rows = db.query(FactMarketDaily).filter_by(asset_id=asset.asset_id).order_by(FactMarketDaily.trade_date).all()
    assert len(rows) == 2
    assert rows[0].knowledge_date.isoformat() == "2021-03-25"
    assert rows[1].knowledge_date.isoformat() == "2026-03-25"
    assert rows[0].source == "fred_backfill"


@respx.mock
def test_backfill_skips_fred_missing_value_marker(db):
    _mock_all_series([
        ("2021-03-25", "2021-01-31", "."),
        ("2026-03-25", "2026-01-31", "312.5"),
    ])

    job.run()

    asset = db.query(DimAsset).filter_by(code="USHPINAT").one()
    rows = db.query(FactMarketDaily).filter_by(asset_id=asset.asset_id).all()
    assert len(rows) == 1
    assert float(rows[0].close) == 312.5


@respx.mock
def test_backfill_ingests_all_nine_series(db):
    _mock_all_series()

    job.run()

    for code in ALL_SERIES:
        asset = db.query(DimAsset).filter_by(code=code).one()
        assert db.query(FactMarketDaily).filter_by(asset_id=asset.asset_id).count() == 2
