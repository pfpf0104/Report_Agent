"""네트워크는 respx로 mock하지만 실제 로컬 Postgres에 쓰는 통합 테스트."""
import io
import zipfile
from datetime import date

import httpx
import pytest
import respx

import app.ingestion.connectors.dart_client as dart_client
import app.ingestion.jobs.backfill_financial_statements as job
from app.db.base import SessionLocal
from app.db.models.dim_asset import DimAsset
from app.db.models.fact_financial_quarterly import FactFinancialQuarterly
from app.db.models.fact_market_daily import FactMarketDaily
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
    # 005930/000660은 fact_market_daily(시세)와 fact_financial_quarterly(BPS)
    # 양쪽에서 참조될 수 있는 실제 운영 데이터가 있는 자산이다 — dim_asset을
    # 지우기 전에 두 fact 테이블 다 정리해야 FK 위반이 안 난다.
    session.query(FactFinancialQuarterly).filter(FactFinancialQuarterly.asset_id.in_(
        session.query(DimAsset.asset_id).filter(DimAsset.code.in_(codes))
    )).delete(synchronize_session=False)
    session.query(FactMarketDaily).filter(FactMarketDaily.asset_id.in_(
        session.query(DimAsset.asset_id).filter(DimAsset.code.in_(codes))
    )).delete(synchronize_session=False)
    session.query(DimAsset).filter(DimAsset.code.in_(codes)).delete(synchronize_session=False)
    session.query(IngestionRun).filter_by(source="backfill_financial_statements").delete()
    session.commit()


@pytest.fixture()
def db():
    from app.ingestion.jobs.ingest_financial_statements import STOCK_CODE

    session = SessionLocal()
    codes = list(STOCK_CODE.values())
    _cleanup(session, codes)
    yield session
    _cleanup(session, codes)
    session.close()


@respx.mock
def test_backfill_sets_knowledge_date_to_actual_filing_date_not_today(db, monkeypatch):
    """핵심 회귀: 과거 연도를 오늘 백필해도 knowledge_date는 오늘이 아니라
    rcept_no에서 뽑은 실제 공시일이어야 한다(회계연도 말+90일 근사가 아님)."""
    monkeypatch.setattr(job, "BACKFILL_YEARS", 1)

    respx.get(CORP_CODE_URL).mock(return_value=httpx.Response(200, content=_corp_code_zip()))
    respx.get(FINANCIALS_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "status": "000",
                "message": "정상",
                "list": [{"account_nm": "자본총계", "thstrm_amount": "486,634,065,000,000", "rcept_no": "20240312000736"}],
            },
        )
    )

    job.run()

    samsung = db.query(DimAsset).filter_by(code="005930").one()
    row = db.query(FactFinancialQuarterly).filter_by(asset_id=samsung.asset_id).one()
    assert row.knowledge_date != date.today()
    assert row.knowledge_date == date(2024, 3, 12)  # rcept_no 앞 8자리
    assert row.source == "dart_backfill"


@respx.mock
def test_backfill_skips_years_already_present_without_calling_dart(db, monkeypatch):
    """핵심 회귀: 재개 가능성. 대상 5개년이 전부 DB에 이미 있으면 DART API를
    아예 호출하지 않아야 한다(재실행 시 쿼터 낭비 방지). corpCode.xml 호출조차
    없어야 하므로, mock을 등록하지 않고 실제로 호출되면 respx가 예외를 낸다."""
    samsung = DimAsset(asset_type="EQUITY", code="005930", name_kr="삼성전자", currency="KRW")
    hynix = DimAsset(asset_type="EQUITY", code="000660", name_kr="SK하이닉스", currency="KRW")
    db.add_all([samsung, hynix])
    db.commit()
    db.refresh(samsung)
    db.refresh(hynix)

    current_year = date.today().year
    for asset in (samsung, hynix):
        for offset in range(1, job.BACKFILL_YEARS + 1):
            db.add(
                FactFinancialQuarterly(
                    asset_id=asset.asset_id, fiscal_year=current_year - offset, fiscal_quarter=4,
                    knowledge_date=date(current_year - offset + 1, 3, 12), bps=99999.0, source="manual_seed",
                )
            )
    db.commit()

    # respx.mock()이 활성화된 상태에서 등록되지 않은 URL이 호출되면
    # respx.MockUnmatchedRequestError(ConnectionError 계열)가 즉시 발생한다 —
    # job.run()이 예외 없이 끝나야 API를 호출하지 않았다는 뜻이다.
    job.run()

    row = db.query(FactFinancialQuarterly).filter_by(asset_id=samsung.asset_id, fiscal_year=current_year - 1).one()
    assert row.source == "manual_seed"  # 덮어쓰지 않았어야 한다
    assert float(row.bps) == 99999.0


@respx.mock
def test_backfill_only_fetches_missing_years(db):
    """일부 연도만 DB에 있으면, 그 연도는 건너뛰고 나머지만 조회한다."""
    samsung = DimAsset(asset_type="EQUITY", code="005930", name_kr="삼성전자", currency="KRW")
    db.add(samsung)
    db.commit()
    db.refresh(samsung)

    current_year = date.today().year
    existing_year = current_year - 1
    db.add(
        FactFinancialQuarterly(
            asset_id=samsung.asset_id, fiscal_year=existing_year, fiscal_quarter=4,
            knowledge_date=date(existing_year + 1, 3, 12), bps=99999.0, source="manual_seed",
        )
    )
    db.commit()

    respx.get(CORP_CODE_URL).mock(return_value=httpx.Response(200, content=_corp_code_zip()))
    respx.get(FINANCIALS_URL, params={"corp_code": "00126380", "bsns_year": str(existing_year)}).mock(
        side_effect=AssertionError("이미 있는 연도는 조회하면 안 된다")
    )
    respx.get(FINANCIALS_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "status": "000",
                "message": "정상",
                "list": [{"account_nm": "자본총계", "thstrm_amount": "1,000,000,000", "rcept_no": "20240312000736"}],
            },
        )
    )

    job.run()

    existing_row = db.query(FactFinancialQuarterly).filter_by(
        asset_id=samsung.asset_id, fiscal_year=existing_year
    ).one()
    assert existing_row.source == "manual_seed"

    new_rows = db.query(FactFinancialQuarterly).filter(
        FactFinancialQuarterly.asset_id == samsung.asset_id, FactFinancialQuarterly.fiscal_year != existing_year
    ).all()
    assert len(new_rows) > 0
    assert all(r.source == "dart_backfill" for r in new_rows)
