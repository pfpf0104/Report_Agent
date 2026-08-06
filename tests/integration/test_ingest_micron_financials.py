"""네트워크는 respx로 mock하지만 실제 로컬 Postgres에 쓰는 통합 테스트.

FMP key-metrics에서 SEC EDGAR로 전환한 이유는 job docstring 참고 — 2026-08
라이브 검증에서 FMP가 MU 심볼만 402로 막고 있음을 확인했다(다른 대형주는
정상 응답). SEC EDGAR는 완전 무료 공식 정부 소스다.
"""
import httpx
import pytest
import respx

import app.ingestion.connectors.sec_edgar_client as sec_edgar_client
import app.ingestion.jobs.ingest_micron_financials as job
from app.db.base import SessionLocal
from app.db.models.dim_asset import DimAsset
from app.db.models.fact_financial_quarterly import FactFinancialQuarterly
from app.db.models.ingestion_run import IngestionRun

TEST_CIK = "0000723125"
FACTS_URL_TEMPLATE = "https://data.sec.gov/api/xbrl/companyconcept/CIK{cik}/us-gaap/{tag}.json"


@pytest.fixture(autouse=True)
def _set_user_agent(monkeypatch):
    monkeypatch.setattr(sec_edgar_client.settings, "sec_edgar_user_agent", "Test Agent test@example.com")


def _cleanup(session):
    ids = session.query(DimAsset.asset_id).filter(DimAsset.code == job.MICRON_SYMBOL)
    session.query(FactFinancialQuarterly).filter(FactFinancialQuarterly.asset_id.in_(ids)).delete(
        synchronize_session=False
    )
    session.query(DimAsset).filter(DimAsset.code == job.MICRON_SYMBOL).delete(synchronize_session=False)
    session.query(IngestionRun).filter_by(source="sec_edgar_micron_financials").delete()
    session.commit()


@pytest.fixture()
def db():
    session = SessionLocal()
    _cleanup(session)
    yield session
    _cleanup(session)
    session.close()


def _mock_concept(tag: str, rows: list[dict], *, unit: str = "USD") -> None:
    respx.get(FACTS_URL_TEMPLATE.format(cik=TEST_CIK, tag=tag)).mock(
        return_value=httpx.Response(200, json={"units": {unit: rows}})
    )


def _equity_row(fy: int, fp: str, end: str, val: int, filed: str) -> dict:
    return {"end": end, "val": val, "fy": fy, "fp": fp, "filed": filed, "form": "10-Q"}


def _income_row(fy: int, fp: str, start: str, end: str, val: int, filed: str) -> dict:
    return {"start": start, "end": end, "val": val, "fy": fy, "fp": fp, "filed": filed, "form": "10-Q"}


def _shares_row(fy: int, fp: str, end: str, val: int, filed: str) -> dict:
    return {"end": end, "val": val, "fy": fy, "fp": fp, "filed": filed, "form": "10-Q"}


@respx.mock
def test_run_computes_bps_and_roe_from_equity_income_shares(db):
    _mock_concept("StockholdersEquity", [
        _equity_row(2026, "Q2", "2026-02-26", 72_459_000_000, "2026-03-19"),
    ])
    _mock_concept("NetIncomeLoss", [
        _income_row(2026, "Q2", "2025-11-28", "2026-02-26", 13_785_000_000, "2026-03-19"),
    ])
    _mock_concept("CommonStockSharesOutstanding", [
        _shares_row(2026, "Q2", "2026-02-26", 1_128_000_000, "2026-03-19"),
    ], unit="shares")

    job.run(cik=TEST_CIK)

    asset = db.query(DimAsset).filter_by(code="MU").one()
    row = db.query(FactFinancialQuarterly).filter_by(asset_id=asset.asset_id).one()
    assert row.fiscal_year == 2026
    assert row.fiscal_quarter == 2
    # bps 컬럼은 Numeric(12, 2) — DB가 소수점 2자리로 반올림해 저장한다.
    assert float(row.bps) == pytest.approx(72_459_000_000 / 1_128_000_000, abs=0.01)
    assert float(row.roe) == pytest.approx(13_785_000_000 / 72_459_000_000, abs=1e-4)
    assert row.knowledge_date.isoformat() == "2026-03-19"
    assert row.source == "sec_edgar"
    assert asset.asset_type == "EQUITY"


@respx.mock
def test_run_dedupes_same_fiscal_period_reported_multiple_times(db):
    """SEC가 같은 (fy, fp)에 당기 값과 비교용 전년동기 값을 함께 실어도,
    실제 마감일(end가 가장 늦은 행)만 반영돼야 한다 — 2026-08 라이브 검증에서
    발견한 회귀(다른 end 값이면 UniqueViolation 또는 잘못된 BPS 고정)."""
    _mock_concept("StockholdersEquity", [
        _equity_row(2026, "Q3", "2025-08-28", 54_165_000_000, "2026-06-25"),  # 전년동기(당기 아님)
        _equity_row(2026, "Q3", "2026-05-28", 100_724_000_000, "2026-06-25"),  # 당기
    ])
    _mock_concept("NetIncomeLoss", [
        _income_row(2026, "Q3", "2026-02-27", "2026-05-28", 28_243_000_000, "2026-06-25"),
    ])
    _mock_concept("CommonStockSharesOutstanding", [
        _shares_row(2026, "Q3", "2026-05-28", 1_129_000_000, "2026-06-25"),
    ], unit="shares")

    job.run(cik=TEST_CIK)

    asset = db.query(DimAsset).filter_by(code="MU").one()
    rows = db.query(FactFinancialQuarterly).filter_by(asset_id=asset.asset_id).all()
    assert len(rows) == 1  # 중복 INSERT가 아니라 한 행으로 합쳐져야 한다
    assert float(rows[0].bps) == pytest.approx(100_724_000_000 / 1_129_000_000, abs=0.01)


@respx.mock
def test_run_skips_periods_missing_shares_data(db):
    _mock_concept("StockholdersEquity", [
        _equity_row(2010, "Q1", "2009-12-03", 1_000_000_000, "2010-01-12"),
    ])
    _mock_concept("NetIncomeLoss", [])
    _mock_concept("CommonStockSharesOutstanding", [], unit="shares")

    job.run(cik=TEST_CIK)

    asset = db.query(DimAsset).filter_by(code="MU").one()
    assert db.query(FactFinancialQuarterly).filter_by(asset_id=asset.asset_id).count() == 0


@respx.mock
def test_run_leaves_roe_null_when_no_matching_income_row(db):
    _mock_concept("StockholdersEquity", [
        _equity_row(2026, "Q2", "2026-02-26", 72_459_000_000, "2026-03-19"),
    ])
    _mock_concept("NetIncomeLoss", [])
    _mock_concept("CommonStockSharesOutstanding", [
        _shares_row(2026, "Q2", "2026-02-26", 1_128_000_000, "2026-03-19"),
    ], unit="shares")

    job.run(cik=TEST_CIK)

    asset = db.query(DimAsset).filter_by(code="MU").one()
    row = db.query(FactFinancialQuarterly).filter_by(asset_id=asset.asset_id).one()
    assert row.roe is None
    assert row.bps is not None


@respx.mock
def test_run_is_idempotent_on_rerun(db):
    _mock_concept("StockholdersEquity", [
        _equity_row(2026, "Q2", "2026-02-26", 72_459_000_000, "2026-03-19"),
    ])
    _mock_concept("NetIncomeLoss", [
        _income_row(2026, "Q2", "2025-11-28", "2026-02-26", 13_785_000_000, "2026-03-19"),
    ])
    _mock_concept("CommonStockSharesOutstanding", [
        _shares_row(2026, "Q2", "2026-02-26", 1_128_000_000, "2026-03-19"),
    ], unit="shares")

    job.run(cik=TEST_CIK)
    job.run(cik=TEST_CIK)

    asset = db.query(DimAsset).filter_by(code="MU").one()
    assert db.query(FactFinancialQuarterly).filter_by(asset_id=asset.asset_id).count() == 1


@respx.mock
def test_run_records_success_status(db):
    _mock_concept("StockholdersEquity", [
        _equity_row(2026, "Q2", "2026-02-26", 72_459_000_000, "2026-03-19"),
    ])
    _mock_concept("NetIncomeLoss", [])
    _mock_concept("CommonStockSharesOutstanding", [
        _shares_row(2026, "Q2", "2026-02-26", 1_128_000_000, "2026-03-19"),
    ], unit="shares")

    job.run(cik=TEST_CIK)

    run_log = db.query(IngestionRun).filter_by(source="sec_edgar_micron_financials").order_by(IngestionRun.id.desc()).first()
    assert run_log.status == "success"
