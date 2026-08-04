"""industry_context.py — 산업·경쟁 분석(Phase 4-4).

마이크론(MU) BPS는 실측(FMP), 삼성전자·SK하이닉스 BPS는 build_valuation_context가
이미 계산한 company dict를 그대로 받는다(재계산하지 않는다) — 여기서는
_get_micron_bps의 point-in-time/폴백 동작과 build_industry_context의 shape만
검증한다.
"""
from datetime import date

import pytest

from app.computation.valuation.industry_context import build_industry_context
from app.db.base import SessionLocal
from app.db.models.dim_asset import AssetType, DimAsset
from app.db.models.fact_financial_quarterly import FactFinancialQuarterly

MICRON_CODE = "MU"


def _cleanup(session):
    ids = session.query(DimAsset.asset_id).filter(DimAsset.code == MICRON_CODE)
    session.query(FactFinancialQuarterly).filter(FactFinancialQuarterly.asset_id.in_(ids)).delete(
        synchronize_session=False
    )
    session.query(DimAsset).filter(DimAsset.code == MICRON_CODE).delete(synchronize_session=False)
    session.commit()


@pytest.fixture()
def db():
    session = SessionLocal()
    _cleanup(session)
    yield session
    _cleanup(session)
    session.close()


def _get_or_create_micron(session) -> DimAsset:
    asset = session.query(DimAsset).filter_by(code=MICRON_CODE).first()
    if asset is not None:
        return asset
    asset = DimAsset(asset_type=AssetType.EQUITY.value, code=MICRON_CODE, name_kr="마이크론(Micron)", currency="USD")
    session.add(asset)
    session.commit()
    session.refresh(asset)
    return asset


_SAMSUNG_STUB = {"book_value": 81_500.0, "book_value_source": "보고서 고정값(DART 데이터 없음)"}
_HYNIX_STUB = {"book_value": 364_000.0, "book_value_source": "보고서 고정값(DART 데이터 없음)"}


def test_build_industry_context_falls_back_when_micron_data_missing(db):
    ctx = build_industry_context(db, date(2026, 7, 30), _SAMSUNG_STUB, _HYNIX_STUB)

    assert ctx["industry_available"] is True
    assert ctx["industry_micron_available"] is False
    assert "industry_micron_data_status" in ctx
    assert len(ctx["industry_structure_cards"]) > 0


def test_build_industry_context_uses_real_micron_bps_when_available(db):
    asset = _get_or_create_micron(db)
    db.add(
        FactFinancialQuarterly(
            asset_id=asset.asset_id, fiscal_year=2026, fiscal_quarter=2,
            knowledge_date=date(2026, 4, 6), bps=44.1, roe=0.15, source="fmp",
        )
    )
    db.commit()

    ctx = build_industry_context(db, date(2026, 7, 30), _SAMSUNG_STUB, _HYNIX_STUB)

    assert ctx["industry_micron_available"] is True
    rows = {row[0]: row for row in ctx["industry_bps_rows"]}
    assert "$44.10" in rows["마이크론"][1]
    assert "삼성전자" in rows
    assert "SK하이닉스" in rows
    assert "industry_bps_disclosure" in ctx


def test_build_industry_context_respects_point_in_time_cutoff(db):
    """as_of보다 나중에 알려진 마이크론 실적은 보이면 안 된다."""
    asset = _get_or_create_micron(db)
    db.add(
        FactFinancialQuarterly(
            asset_id=asset.asset_id, fiscal_year=2026, fiscal_quarter=3,
            knowledge_date=date(2026, 12, 1), bps=45.2, roe=0.18, source="fmp",
        )
    )
    db.commit()

    ctx = build_industry_context(db, date(2026, 7, 30), _SAMSUNG_STUB, _HYNIX_STUB)

    assert ctx["industry_micron_available"] is False


def test_build_industry_context_picks_latest_quarter(db):
    asset = _get_or_create_micron(db)
    db.bulk_save_objects([
        FactFinancialQuarterly(
            asset_id=asset.asset_id, fiscal_year=2026, fiscal_quarter=1,
            knowledge_date=date(2026, 1, 5), bps=40.0, roe=0.10, source="fmp",
        ),
        FactFinancialQuarterly(
            asset_id=asset.asset_id, fiscal_year=2026, fiscal_quarter=2,
            knowledge_date=date(2026, 4, 6), bps=44.1, roe=0.15, source="fmp",
        ),
    ])
    db.commit()

    ctx = build_industry_context(db, date(2026, 7, 30), _SAMSUNG_STUB, _HYNIX_STUB)

    rows = {row[0]: row for row in ctx["industry_bps_rows"]}
    assert "$44.10" in rows["마이크론"][1]
    assert "Q2" in rows["마이크론"][2]
