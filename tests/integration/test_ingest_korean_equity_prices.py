"""네트워크는 respx로 mock하지만 실제 로컬 Postgres에 쓰는 통합 테스트."""
import httpx
import pytest
import respx

import app.ingestion.jobs.ingest_korean_equity_prices as job
from app.db.base import SessionLocal
from app.db.models.dim_asset import DimAsset
from app.db.models.fact_financial_quarterly import FactFinancialQuarterly
from app.db.models.fact_market_daily import FactMarketDaily
from app.db.models.ingestion_run import IngestionRun

TOKEN_URL = "https://openapivts.koreainvestment.com:29443/oauth2/tokenP"
PRICE_URL = "https://openapivts.koreainvestment.com:29443/uapi/domestic-stock/v1/quotations/inquire-price"


@pytest.fixture(autouse=True)
def _set_credentials(monkeypatch):
    import app.ingestion.connectors.kis_client as kis_client

    monkeypatch.setattr(kis_client.settings, "kis_app_key", "test-app-key")
    monkeypatch.setattr(kis_client.settings, "kis_app_secret", "test-app-secret")
    monkeypatch.setattr(kis_client.settings, "kis_base_url", "https://openapivts.koreainvestment.com:29443")
    kis_client._token_cache.clear()
    yield
    kis_client._token_cache.clear()


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
    session.query(IngestionRun).filter_by(source="kis_korean_equity_prices").delete()
    session.commit()


@pytest.fixture()
def db():
    session = SessionLocal()
    codes = list(job.SYMBOLS.keys())
    # 실제 운영 인제스천(Phase 0-1 등)이 같은 code로 dim_asset을 먼저 만들어뒀을 수
    # 있다 — teardown뿐 아니라 setup에서도 정리해야 unique constraint 충돌이 없다.
    _cleanup(session, codes)
    yield session
    _cleanup(session, codes)
    session.close()


@respx.mock
def test_run_upserts_prices_into_fact_market_daily(db):
    respx.post(TOKEN_URL).mock(return_value=httpx.Response(200, json={"access_token": "tok", "expires_in": 86400}))
    # job.SYMBOLS는 삼성전자·SK하이닉스·통안채1년·국고채3년 4개다 — 심볼 수만큼
    # 순서대로 응답해야 한다(_fetch_all_prices가 SYMBOLS 순서로 호출한다).
    respx.get(PRICE_URL).mock(
        side_effect=[
            httpx.Response(200, json={"rt_cd": "0", "output": {"stck_prpr": "208500", "acml_vol": "1000"}}),
            httpx.Response(200, json={"rt_cd": "0", "output": {"stck_prpr": "1401000", "acml_vol": "500"}}),
            httpx.Response(200, json={"rt_cd": "0", "output": {"stck_prpr": "10250", "acml_vol": "300"}}),
            httpx.Response(200, json={"rt_cd": "0", "output": {"stck_prpr": "10420", "acml_vol": "200"}}),
        ]
    )

    job.run()

    samsung = db.query(DimAsset).filter_by(code="005930").one()
    row = db.query(FactMarketDaily).filter_by(asset_id=samsung.asset_id).one()
    assert float(row.close) == 208500
    assert row.source == "kis"

    run_log = db.query(IngestionRun).filter_by(source="kis_korean_equity_prices").order_by(IngestionRun.id.desc()).first()
    assert run_log.status == "success"


@respx.mock
def test_run_records_failure_on_api_error(db):
    respx.post(TOKEN_URL).mock(return_value=httpx.Response(200, json={"access_token": "tok", "expires_in": 86400}))
    respx.get(PRICE_URL).mock(return_value=httpx.Response(200, json={"rt_cd": "1", "msg1": "오류"}))

    with pytest.raises(Exception):
        job.run()

    run_log = db.query(IngestionRun).filter_by(source="kis_korean_equity_prices").order_by(IngestionRun.id.desc()).first()
    assert run_log.status == "failed"
