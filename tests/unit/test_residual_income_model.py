from datetime import date

import pytest

from app.computation.valuation.residual_income_model import (
    SAMSUNG_BOOK_VALUE,
    SAMSUNG_SCENARIOS,
    SK_HYNIX_BOOK_VALUE,
    SK_HYNIX_SCENARIOS,
    build_valuation_context,
    probability_weighted_value,
)
from app.db.base import SessionLocal
from app.db.models.dim_asset import AssetType, DimAsset
from app.db.models.fact_market_daily import FactMarketDaily

# 첨부 밸류에이션 보고서 원문 값(오차 수십 원 이내로 검산 완료 — CHANGELOG 커밋 참고).
SAMSUNG_TARGETS = {"제한적 추격": 384793, "점진적 추격": 229640, "공격적 추격": 127096, "가격전쟁": 89791}
HYNIX_TARGETS = {"제한적 추격": 2914632, "점진적 추격": 1565808, "공격적 추격": 706372, "가격전쟁": 454656}


@pytest.mark.parametrize("targets,book_value,scenarios", [
    (SAMSUNG_TARGETS, SAMSUNG_BOOK_VALUE, SAMSUNG_SCENARIOS),
    (HYNIX_TARGETS, SK_HYNIX_BOOK_VALUE, SK_HYNIX_SCENARIOS),
])
def test_rim_values_match_reference_report(targets, book_value, scenarios):
    result = probability_weighted_value(book_value, scenarios)
    for row in result["rows"]:
        target = targets[row["scenario"]]
        assert row["value"] == pytest.approx(target, abs=100)  # 원 단위 반올림 오차 허용


@pytest.fixture()
def db():
    session = SessionLocal()
    yield session
    session.query(FactMarketDaily).filter(FactMarketDaily.asset_id.in_(
        session.query(DimAsset.asset_id).filter(DimAsset.code.in_(["005930", "000660"]))
    )).delete(synchronize_session=False)
    session.query(DimAsset).filter(DimAsset.code.in_(["005930", "000660"])).delete(synchronize_session=False)
    session.commit()
    session.close()


def test_build_valuation_context_falls_back_without_kis_data(db):
    context = build_valuation_context(db, date(2026, 7, 30))
    samsung_card = context["cards"][0]
    assert "208,500원" in samsung_card["caption"]
    assert "보고서 고정값" in samsung_card["caption"]


def test_build_valuation_context_prefers_real_kis_price(db):
    asset = DimAsset(asset_type=AssetType.EQUITY.value, code="005930", name_kr="삼성전자", currency="KRW")
    db.add(asset)
    db.commit()
    db.refresh(asset)
    db.add(FactMarketDaily(asset_id=asset.asset_id, trade_date=date(2026, 7, 30), close=220000, adj_close=220000, source="kis"))
    db.commit()

    context = build_valuation_context(db, date(2026, 7, 30))
    samsung_card = context["cards"][0]
    assert "220,000원" in samsung_card["caption"]
    assert "KIS 실시간 시세" in samsung_card["caption"]
