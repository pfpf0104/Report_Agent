"""네트워크는 respx로 mock하지만 실제 로컬 Postgres에 쓰는 통합 테스트."""
import io
import zipfile

import httpx
import pytest
import respx

import app.ingestion.connectors.dart_client as dart_client
import app.ingestion.jobs.ingest_financial_statements as job
from app.db.base import SessionLocal
from app.db.models.dim_asset import DimAsset
from app.db.models.fact_financial_quarterly import FactFinancialQuarterly
from app.db.models.ingestion_run import IngestionRun

CORP_CODE_URL = "https://opendart.fss.or.kr/api/corpCode.xml"
FINANCIALS_URL = "https://opendart.fss.or.kr/api/fnlttSinglAcntAll.json"


def _corp_code_zip() -> bytes:
    xml = (
        "<result>"
        "<list><corp_code>00126380</corp_code><corp_name>삼성전자</corp_name><stock_code>005930</stock_code></list>"
        "<list><corp_code>00164779</corp_code><corp_name>SK하이닉스</corp_name><stock_code>000660</stock_code></list>"
        "</result>"
    ).encode("utf-8")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("CORPCODE.xml", xml)
    return buf.getvalue()


@pytest.fixture(autouse=True)
def _setup(monkeypatch):
    monkeypatch.setattr(dart_client.settings, "dart_api_key", "test-key")
    dart_client._CORP_CODE_CACHE = None
    yield
    dart_client._CORP_CODE_CACHE = None


def _cleanup(session, codes):
    session.query(FactFinancialQuarterly).filter(FactFinancialQuarterly.asset_id.in_(
        session.query(DimAsset.asset_id).filter(DimAsset.code.in_(codes))
    )).delete(synchronize_session=False)
    session.query(DimAsset).filter(DimAsset.code.in_(codes)).delete(synchronize_session=False)
    session.query(IngestionRun).filter_by(source="dart_financial_statements").delete()
    session.commit()


@pytest.fixture()
def db():
    session = SessionLocal()
    codes = list(job.STOCK_CODE.values())
    # 실제 운영 인제스천(Phase 0-1 등)이 같은 code로 dim_asset을 먼저 만들어뒀을 수
    # 있다 — teardown뿐 아니라 setup에서도 정리해야 unique constraint 충돌이 없다.
    _cleanup(session, codes)
    yield session
    _cleanup(session, codes)
    session.close()


@respx.mock
def test_run_computes_bps_and_upserts(db):
    respx.get(CORP_CODE_URL).mock(return_value=httpx.Response(200, content=_corp_code_zip()))
    respx.get(FINANCIALS_URL, params={"corp_code": "00126380"}).mock(
        return_value=httpx.Response(
            200, json={"status": "000", "message": "정상", "list": [{"account_nm": "자본총계", "thstrm_amount": "486,634,065,000,000"}]}
        )
    )
    respx.get(FINANCIALS_URL, params={"corp_code": "00164779"}).mock(
        return_value=httpx.Response(
            200, json={"status": "000", "message": "정상", "list": [{"account_nm": "자본총계", "thstrm_amount": "50,000,000,000,000"}]}
        )
    )

    job.run(bsns_year=2025)

    samsung = db.query(DimAsset).filter_by(code="005930").one()
    row = db.query(FactFinancialQuarterly).filter_by(asset_id=samsung.asset_id).one()
    expected_bps = 486_634_065_000_000 / job.SHARES_OUTSTANDING["삼성전자"]
    assert float(row.bps) == pytest.approx(expected_bps, rel=1e-4)
    assert row.source == "dart"
    assert row.fiscal_year == 2025
    assert row.fiscal_quarter == 4

    run_log = db.query(IngestionRun).filter_by(source="dart_financial_statements").order_by(IngestionRun.id.desc()).first()
    assert run_log.status == "success"


@respx.mock
def test_run_falls_back_to_previous_year_when_report_not_yet_filed(db):
    """as_of가 연초라 대상 연도 사업보고서가 아직 없으면 전년도로 한 번 더 시도한다."""
    respx.get(CORP_CODE_URL).mock(return_value=httpx.Response(200, content=_corp_code_zip()))
    respx.get(FINANCIALS_URL, params={"corp_code": "00126380", "bsns_year": "2026"}).mock(
        return_value=httpx.Response(200, json={"status": "013", "message": "조회된 데이터가 없습니다."})
    )
    respx.get(FINANCIALS_URL, params={"corp_code": "00164779", "bsns_year": "2026"}).mock(
        return_value=httpx.Response(200, json={"status": "013", "message": "조회된 데이터가 없습니다."})
    )
    respx.get(FINANCIALS_URL, params={"corp_code": "00126380", "bsns_year": "2025"}).mock(
        return_value=httpx.Response(
            200, json={"status": "000", "message": "정상", "list": [{"account_nm": "자본총계", "thstrm_amount": "486,634,065,000,000"}]}
        )
    )
    respx.get(FINANCIALS_URL, params={"corp_code": "00164779", "bsns_year": "2025"}).mock(
        return_value=httpx.Response(200, json={"status": "013", "message": "조회된 데이터가 없습니다."})
    )

    job.run(bsns_year=2026)

    samsung = db.query(DimAsset).filter_by(code="005930").one()
    row = db.query(FactFinancialQuarterly).filter_by(asset_id=samsung.asset_id).one()
    assert row.fiscal_year == 2025  # 2026 보고서가 없어 2025로 폴백

    hynix_asset = db.query(DimAsset).filter_by(code="000660").first()
    assert hynix_asset is None  # SK하이닉스는 두 연도 다 데이터가 없어 아예 행이 안 생겨야 한다


@respx.mock
def test_run_records_success_even_when_no_data_found(db):
    """두 연도 다 실패해도 예외를 던지지 않는다(다른 잡들의 관대한 처리와 동일한 패턴) —
    단, 이 경우 아무 행도 적재되지 않는다는 걸 명확히 하는 회귀 테스트."""
    respx.get(CORP_CODE_URL).mock(return_value=httpx.Response(200, content=_corp_code_zip()))
    respx.get(FINANCIALS_URL).mock(
        return_value=httpx.Response(200, json={"status": "013", "message": "조회된 데이터가 없습니다."})
    )

    job.run(bsns_year=2026)

    run_log = db.query(IngestionRun).filter_by(source="dart_financial_statements").order_by(IngestionRun.id.desc()).first()
    assert run_log.status == "success"
    assert db.query(FactFinancialQuarterly).count() == 0
