"""네트워크는 respx로 mock하지만 실제 로컬 Postgres에 쓰는 통합 테스트."""
import httpx
import pytest
import respx

import app.ingestion.jobs.ingest_macro_rates as job
from app.db.base import SessionLocal
from app.db.models.dim_asset import DimAsset
from app.db.models.fact_market_daily import FactMarketDaily
from app.db.models.ingestion_run import IngestionRun


@pytest.fixture(autouse=True)
def _set_api_key(monkeypatch):
    import app.ingestion.connectors.bok_client as bok_client

    monkeypatch.setattr(bok_client.settings, "bok_api_key", "test-key")


@pytest.fixture()
def db():
    session = SessionLocal()
    codes = list(job.MACRO_SERIES.keys())
    yield session
    session.query(FactMarketDaily).filter(FactMarketDaily.asset_id.in_(
        session.query(DimAsset.asset_id).filter(DimAsset.code.in_(codes))
    )).delete(synchronize_session=False)
    session.query(DimAsset).filter(DimAsset.code.in_(codes)).delete(synchronize_session=False)
    session.query(IngestionRun).filter_by(source="bok_macro_rates").delete()
    session.commit()
    session.close()


@respx.mock
def test_run_upserts_macro_yields_into_fact_market_daily(db):
    respx.get(url__regex=r"https://ecos\.bok\.or\.kr/api/StatisticSearch/.*").mock(
        return_value=httpx.Response(
            200, json={"StatisticSearch": {"row": [{"TIME": "20260730", "DATA_VALUE": "3.05"}]}}
        )
    )

    job.run()

    ktb1y = db.query(DimAsset).filter_by(code="KTB1Y").one()
    row = db.query(FactMarketDaily).filter_by(asset_id=ktb1y.asset_id).one()
    assert float(row.close) == 3.05
    assert row.source == "bok_ecos"

    run_log = db.query(IngestionRun).filter_by(source="bok_macro_rates").order_by(IngestionRun.id.desc()).first()
    assert run_log.status == "success"


@respx.mock
def test_run_picks_latest_row_regardless_of_response_order(db):
    """회귀 테스트: ECOS가 오름차순으로 반환한다는 가정에 기대지 않고 TIME 값으로
    직접 최신 행을 고르는지 확인한다(내림차순으로 와도 정답이어야 한다)."""
    respx.get(url__regex=r"https://ecos\.bok\.or\.kr/api/StatisticSearch/.*").mock(
        return_value=httpx.Response(
            200,
            json={
                "StatisticSearch": {
                    "row": [
                        {"TIME": "20260730", "DATA_VALUE": "3.05"},
                        {"TIME": "20260701", "DATA_VALUE": "2.90"},  # 더 오래된 값이 먼저 옴(내림차순 가정 X)
                    ]
                }
            },
        )
    )

    job.run()

    ktb1y = db.query(DimAsset).filter_by(code="KTB1Y").one()
    row = db.query(FactMarketDaily).filter_by(asset_id=ktb1y.asset_id).one()
    assert float(row.close) == 3.05  # 07-30이 07-01보다 최신이므로 이 값이어야 한다


@respx.mock
def test_run_records_failure_on_api_error(db):
    respx.get(url__regex=r"https://ecos\.bok\.or\.kr/api/StatisticSearch/.*").mock(
        return_value=httpx.Response(200, json={"RESULT": {"CODE": "INFO-200", "MESSAGE": "no data"}})
    )

    with pytest.raises(Exception):
        job.run()

    run_log = db.query(IngestionRun).filter_by(source="bok_macro_rates").order_by(IngestionRun.id.desc()).first()
    assert run_log.status == "failed"
