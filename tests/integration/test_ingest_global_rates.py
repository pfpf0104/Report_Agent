"""네트워크는 respx로 mock하지만 실제 로컬 Postgres에 쓰는 통합 테스트."""
import httpx
import pytest
import respx

import app.ingestion.jobs.ingest_global_rates as job
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
    session.query(IngestionRun).filter_by(source="fred_global_rates").delete()
    session.commit()


@pytest.fixture()
def db():
    session = SessionLocal()
    codes = list(job.ALL_SERIES.keys())
    _cleanup(session, codes)
    yield session
    _cleanup(session, codes)
    session.close()


def _obs_response(date_value_pairs: list[tuple[str, str]]) -> dict:
    return {"observations": [{"date": d, "value": v} for d, v in date_value_pairs]}


def _mock_all_series(overrides: dict[str, dict] | None = None) -> None:
    """job.ALL_SERIES(16개)의 모든 series_id에 기본 응답을 mock하고, overrides로
    개별 시리즈만 덮어쓴다."""
    overrides = overrides or {}
    for code, series_id in job.ALL_SERIES.items():
        response = overrides.get(code) or _obs_response([("2026-07-30", "4.00")])
        respx.get(FRED_URL, params={"series_id": series_id}).mock(
            return_value=httpx.Response(200, json=response)
        )


@respx.mock
def test_run_normalizes_rate_series_to_bp(db):
    _mock_all_series({"US10Y": _obs_response([("2026-07-30", "4.68")])})

    job.run()

    asset = db.query(DimAsset).filter_by(code="US10Y").one()
    row = db.query(FactMarketDaily).filter_by(asset_id=asset.asset_id).one()
    # FRED는 퍼센트(4.68)를 주지만 금리류는 bp(468.0)로 정규화해 저장한다.
    assert float(row.close) == 468.0
    assert row.source == "fred"
    assert asset.asset_type == AssetType.MACRO.value


@respx.mock
def test_run_keeps_index_series_in_raw_units(db):
    """스프레드·지수(GLOBAL_INDEX_SERIES)는 bp 정규화 없이 원단위 그대로 저장돼야
    한다 — T10Y2Y는 음수가 될 수 있고 달러지수는 100 안팎의 무차원 값이라
    bp로 바꾸면 의미가 없다."""
    _mock_all_series({"US10Y2Y": _obs_response([("2026-07-31", "-0.15")])})

    job.run()

    asset = db.query(DimAsset).filter_by(code="US10Y2Y").one()
    row = db.query(FactMarketDaily).filter_by(asset_id=asset.asset_id).one()
    assert float(row.close) == -0.15
    assert asset.asset_type == AssetType.MACRO_INDEX.value


@respx.mock
def test_run_skips_fred_missing_value_marker(db):
    """FRED는 결측일(공휴일 등)을 value="."로 표시한다 — 최신 관측치를 고를 때
    이를 건너뛰고 실제 값이 있는 가장 최근 행을 써야 한다."""
    _mock_all_series({
        "US10Y": _obs_response([
            ("2026-07-31", "."),
            ("2026-07-30", "4.68"),
        ])
    })

    job.run()

    asset = db.query(DimAsset).filter_by(code="US10Y").one()
    row = db.query(FactMarketDaily).filter_by(asset_id=asset.asset_id).one()
    assert row.trade_date.isoformat() == "2026-07-30"
    assert float(row.close) == 468.0


@respx.mock
def test_run_inserts_all_sixteen_series(db):
    """핵심 회귀: 16개 시리즈(금리 12개+지수 4개) 전부 적재돼야 한다."""
    _mock_all_series()

    job.run()

    for code in job.ALL_SERIES:
        asset = db.query(DimAsset).filter_by(code=code).one()
        row = db.query(FactMarketDaily).filter_by(asset_id=asset.asset_id).one()
        assert row.source == "fred"

    assert len(job.ALL_SERIES) == 16
