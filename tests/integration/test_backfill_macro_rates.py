"""네트워크는 respx로 mock하지만 실제 로컬 Postgres에 쓰는 통합 테스트."""
from datetime import date

import httpx
import pytest
import respx

import app.ingestion.jobs.backfill_macro_rates as job
from app.db.base import SessionLocal
from app.db.models.dim_asset import DimAsset
from app.db.models.fact_market_daily import FactMarketDaily
from app.db.models.ingestion_run import IngestionRun


@pytest.fixture(autouse=True)
def _set_api_key(monkeypatch):
    import app.ingestion.connectors.bok_client as bok_client

    monkeypatch.setattr(bok_client.settings, "bok_api_key", "test-key")


def _cleanup(session, codes):
    session.query(FactMarketDaily).filter(FactMarketDaily.asset_id.in_(
        session.query(DimAsset.asset_id).filter(DimAsset.code.in_(codes))
    )).delete(synchronize_session=False)
    session.query(DimAsset).filter(DimAsset.code.in_(codes)).delete(synchronize_session=False)
    session.query(IngestionRun).filter_by(source="backfill_macro_rates").delete()
    session.commit()


@pytest.fixture()
def db():
    session = SessionLocal()
    codes = list(job.MACRO_SERIES.keys())
    _cleanup(session, codes)
    yield session
    _cleanup(session, codes)
    session.close()


def _mock_year_response(rows: list[dict]):
    return httpx.Response(200, json={"StatisticSearch": {"row": rows}})


@respx.mock
def test_backfill_normalizes_percent_to_bp_and_sets_knowledge_date(db, monkeypatch):
    """단일 연도만 실제로 데이터를 반환하도록 좁혀서 정규화·knowledge_date만 확인한다."""
    monkeypatch.setattr(job, "BACKFILL_YEARS", 1)
    respx.get(url__regex=r"https://ecos\.bok\.or\.kr/api/StatisticSearch/.*").mock(
        return_value=_mock_year_response([{"TIME": "20260115", "DATA_VALUE": "3.20"}])
    )

    job.run()

    ktb1y = db.query(DimAsset).filter_by(code="KTB1Y").one()
    row = db.query(FactMarketDaily).filter_by(asset_id=ktb1y.asset_id).one()
    assert float(row.close) == 320.0  # 3.20% -> 320bp
    assert row.trade_date == date(2026, 1, 15)
    assert row.knowledge_date == date(2026, 1, 15)  # 일별 금리는 당일 공표
    assert row.source == "bok_ecos_backfill"


@respx.mock
def test_backfill_skips_dates_already_present(db, monkeypatch):
    """재개 가능성: 이미 있는 trade_date는 건너뛰고 값을 덮어쓰지 않는다."""
    monkeypatch.setattr(job, "BACKFILL_YEARS", 1)
    ktb1y = DimAsset(asset_type="MACRO", code="KTB1Y", name_kr="KTB1Y", currency="KRW")
    db.add(ktb1y)
    db.commit()
    db.refresh(ktb1y)
    db.add(
        FactMarketDaily(
            asset_id=ktb1y.asset_id, trade_date=date(2026, 1, 15), knowledge_date=date(2026, 1, 15),
            close=999.0, adj_close=999.0, source="manual_seed",
        )
    )
    db.commit()

    respx.get(url__regex=r"https://ecos\.bok\.or\.kr/api/StatisticSearch/.*").mock(
        return_value=_mock_year_response([{"TIME": "20260115", "DATA_VALUE": "3.20"}])
    )

    job.run()

    row = db.query(FactMarketDaily).filter_by(asset_id=ktb1y.asset_id, trade_date=date(2026, 1, 15)).one()
    assert float(row.close) == 999.0  # 덮어쓰지 않았어야 한다
    assert row.source == "manual_seed"
