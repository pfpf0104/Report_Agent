"""Point-in-time 정합성 회귀 테스트.

리포트 본문은 "정보 동결", "7일 embargo" 같은 기관급 규율을 주장해 왔지만,
knowledge_date 컬럼이 도입되기 전에는 스키마가 이를 강제할 수 없었다.
아래 테스트는 그 규율이 실제로 코드에서 지켜지는지 확인한다.
"""
from datetime import date

import pytest

from app.computation.valuation.residual_income_model import (
    SAMSUNG_BOOK_VALUE,
    CURRENT_PRICE_FALLBACK,
    build_valuation_context,
)
from app.db.base import SessionLocal
from app.db.models.dim_asset import AssetType, DimAsset
from app.db.models.fact_financial_quarterly import FactFinancialQuarterly
from app.db.models.fact_market_daily import FactMarketDaily
from app.db.point_in_time import visible_as_of

SAMSUNG = "005930"


def _cleanup(session):
    session.query(FactMarketDaily).filter(FactMarketDaily.asset_id.in_(
        session.query(DimAsset.asset_id).filter(DimAsset.code == SAMSUNG)
    )).delete(synchronize_session=False)
    session.query(FactFinancialQuarterly).filter(FactFinancialQuarterly.asset_id.in_(
        session.query(DimAsset.asset_id).filter(DimAsset.code == SAMSUNG)
    )).delete(synchronize_session=False)
    session.query(DimAsset).filter(DimAsset.code == SAMSUNG).delete(synchronize_session=False)
    session.commit()


@pytest.fixture()
def db():
    session = SessionLocal()
    # 실제 운영 인제스천(Phase 0-1 등)이 같은 code(005930)로 실데이터를 이미
    # 적재했을 수 있다 — teardown뿐 아니라 setup에서도 정리해야 이 테스트가
    # "삼성전자 fact 행이 정확히 1건"이라고 가정하는 assert들이 성립한다.
    _cleanup(session)
    yield session
    _cleanup(session)
    session.close()


def _samsung(db) -> DimAsset:
    asset = DimAsset(asset_type=AssetType.EQUITY.value, code=SAMSUNG, name_kr="삼성전자", currency="KRW")
    db.add(asset)
    db.commit()
    db.refresh(asset)
    return asset


def test_visible_as_of_excludes_rows_known_only_later(db):
    asset = _samsung(db)
    db.add(FactMarketDaily(
        asset_id=asset.asset_id, trade_date=date(2026, 1, 5), knowledge_date=date(2026, 6, 1),
        close=100.0, adj_close=100.0, source="test",
    ))
    db.commit()

    # 사건일(2026-01-05)은 지났지만 취득일(2026-06-01)은 아직인 시점
    visible = visible_as_of(db.query(FactMarketDaily), FactMarketDaily, date(2026, 3, 1)).all()
    assert visible == []

    # 취득일 이후에는 보인다
    visible = visible_as_of(db.query(FactMarketDaily), FactMarketDaily, date(2026, 6, 1)).all()
    assert len(visible) == 1


def test_visible_as_of_none_disables_filter(db):
    asset = _samsung(db)
    db.add(FactMarketDaily(
        asset_id=asset.asset_id, trade_date=date(2026, 1, 5), knowledge_date=date(2099, 1, 1),
        close=100.0, adj_close=100.0, source="test",
    ))
    db.commit()

    assert len(visible_as_of(db.query(FactMarketDaily), FactMarketDaily, None).all()) == 1


def test_valuation_ignores_bps_not_yet_disclosed_at_as_of(db):
    """핵심 회귀 테스트: 회계연도(2025)는 지났지만 공시(2026-03-31) 전인 시점에
    2025 BPS를 쓰면 안 된다. knowledge_date 도입 전에는 이 구분이 불가능했다."""
    asset = _samsung(db)
    db.add(FactFinancialQuarterly(
        asset_id=asset.asset_id, fiscal_year=2025, fiscal_quarter=4,
        knowledge_date=date(2026, 3, 31), bps=90000.0, source="dart",
    ))
    db.commit()

    # 공시 전(2026-02-01): 아직 알 수 없으므로 폴백을 써야 한다
    before = build_valuation_context(db, date(2026, 2, 1))
    assert before["samsung"]["book_value"] == pytest.approx(SAMSUNG_BOOK_VALUE)
    assert "보고서 고정값" in before["samsung"]["book_value_source"]

    # 공시 후(2026-07-30): 실측값을 써야 한다
    after = build_valuation_context(db, date(2026, 7, 30))
    assert after["samsung"]["book_value"] == pytest.approx(90000.0)
    assert "DART 2025년" in after["samsung"]["book_value_source"]


def test_valuation_ignores_price_not_yet_knowable_at_as_of(db):
    asset = _samsung(db)
    db.add(FactMarketDaily(
        asset_id=asset.asset_id, trade_date=date(2026, 7, 30), knowledge_date=date(2026, 7, 30),
        close=220000, adj_close=220000, source="kis",
    ))
    db.commit()

    # 시세 취득일 이전 기준일에서는 이 가격이 보이면 안 된다
    before = build_valuation_context(db, date(2026, 6, 30))
    assert before["samsung"]["current_price"] == pytest.approx(CURRENT_PRICE_FALLBACK["삼성전자"])
    assert "보고서 고정값" in before["samsung"]["price_source"]

    after = build_valuation_context(db, date(2026, 7, 30))
    assert after["samsung"]["current_price"] == pytest.approx(220000.0)
    assert "KIS" in after["samsung"]["price_source"]
