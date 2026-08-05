"""네트워크는 respx로 mock하지만 실제 로컬 Postgres에 쓰는 통합 테스트."""
import httpx
import pytest
import respx

import app.ingestion.jobs.ingest_housing_indicators as job
from app.db.base import SessionLocal
from app.db.models.dim_asset import AssetType, DimAsset
from app.db.models.fact_market_daily import FactMarketDaily
from app.db.models.ingestion_run import IngestionRun

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
    session.query(IngestionRun).filter_by(source="fred_housing_indicators").delete()
    session.commit()


@pytest.fixture()
def db():
    session = SessionLocal()
    codes = list(job.ALL_SERIES.keys())
    _cleanup(session, codes)
    yield session
    _cleanup(session, codes)
    session.close()


def _vintage_response(rows: list[tuple[str, str, str]]) -> dict:
    """output_type=4 응답 형식 — 각 행이 (realtime_start, date, value)."""
    return {
        "observations": [
            {"realtime_start": rs, "realtime_end": "9999-12-31", "date": d, "value": v}
            for rs, d, v in rows
        ]
    }


def _mock_all_series(overrides: dict[str, dict] | None = None) -> None:
    overrides = overrides or {}
    for code, series_id in job.ALL_SERIES.items():
        response = overrides.get(code) or _vintage_response(
            [("2026-03-25", "2026-01-31", "310.5")]
        )
        respx.get(FRED_URL, params={"series_id": series_id}).mock(
            return_value=httpx.Response(200, json=response)
        )


@respx.mock
def test_run_uses_realtime_start_as_knowledge_date(db):
    """관측월(date)이 아니라 최초 공표일(realtime_start)이 knowledge_date여야
    한다 — Case-Shiller 등은 관측월과 발표일 사이 약 2개월 지연이 있다."""
    _mock_all_series({
        "USHPINAT": _vintage_response([("2026-03-25", "2026-01-31", "312.45")])
    })

    job.run()

    asset = db.query(DimAsset).filter_by(code="USHPINAT").one()
    row = db.query(FactMarketDaily).filter_by(asset_id=asset.asset_id).one()
    assert row.trade_date.isoformat() == "2026-01-31"
    assert row.knowledge_date.isoformat() == "2026-03-25"
    assert float(row.close) == 312.45
    assert row.source == "fred"
    assert asset.asset_type == AssetType.MACRO_ECONOMIC.value


@respx.mock
def test_run_skips_fred_missing_value_marker(db):
    _mock_all_series({
        "USHPILA": _vintage_response([
            ("2026-01-01", "2025-12-31", "."),
            ("2026-02-25", "2026-01-31", "450.0"),
        ])
    })

    job.run()

    asset = db.query(DimAsset).filter_by(code="USHPILA").one()
    rows = db.query(FactMarketDaily).filter_by(asset_id=asset.asset_id).all()
    assert len(rows) == 1
    assert float(rows[0].close) == 450.0


@respx.mock
def test_run_ingests_both_national_and_city_series(db):
    """전국 3개 + 도시 6개 = 9개 전부 적재돼야 한다."""
    _mock_all_series()

    job.run()

    for code in job.ALL_SERIES:
        asset = db.query(DimAsset).filter_by(code=code).one()
        row = db.query(FactMarketDaily).filter_by(asset_id=asset.asset_id).one()
        assert row.source == "fred"

    assert len(job.NATIONAL_HOUSING_SERIES) == 3
    assert len(job.CITY_HOUSING_SERIES) == 6
    assert len(job.ALL_SERIES) == 9


@respx.mock
def test_run_records_failure_on_api_error(db):
    for series_id in job.ALL_SERIES.values():
        respx.get(FRED_URL, params={"series_id": series_id}).mock(
            return_value=httpx.Response(200, json={"error_message": "Bad Request"})
        )

    with pytest.raises(Exception):
        job.run()

    run_log = db.query(IngestionRun).filter_by(source="fred_housing_indicators").order_by(IngestionRun.id.desc()).first()
    assert run_log.status == "failed"
