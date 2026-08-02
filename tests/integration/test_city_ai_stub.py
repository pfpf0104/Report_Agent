"""city_ai_stub — G13: 금리커브(yield_1y_bp/yield_3y_bp)는 실측 우선, 없으면 합성 폴백.

DB 왕복이 필요해 통합 테스트로 둔다. 여기서 확인할 것은 세 가지다.
  1) DB에 KTB1Y/KTB3Y가 있으면 그 bp 값을 그대로 쓰는가(단위 변환을 다시 하지 않는가).
  2) DB에 없으면(백필 전 구간) 합성 폴백으로 조용히 넘어가는가.
  3) predicted_change_bp는 실측 여부와 무관하게 항상 합성인가(아직 City AI 모델이 없으므로).
"""
from datetime import date

import pytest

from app.computation.fixed_income.city_ai_stub import synthetic_city_ai_output
from app.db.base import SessionLocal
from app.db.models.dim_asset import AssetType, DimAsset
from app.db.models.fact_market_daily import FactMarketDaily

CODES = ["KTB1Y", "KTB3Y"]


def _cleanup(session):
    ids = session.query(DimAsset.asset_id).filter(DimAsset.code.in_(CODES))
    session.query(FactMarketDaily).filter(FactMarketDaily.asset_id.in_(ids)).delete(
        synchronize_session=False
    )
    session.query(DimAsset).filter(DimAsset.code.in_(CODES)).delete(synchronize_session=False)
    session.commit()


@pytest.fixture()
def db():
    session = SessionLocal()
    _cleanup(session)
    yield session
    _cleanup(session)
    session.close()


def _seed_rate(session, code: str, trade_date: date, bp_value: float) -> None:
    asset = DimAsset(asset_type=AssetType.MACRO.value, code=code, name_kr=code, currency="KRW")
    session.add(asset)
    session.commit()
    session.refresh(asset)
    session.add(
        FactMarketDaily(
            asset_id=asset.asset_id,
            trade_date=trade_date,
            knowledge_date=trade_date,
            close=bp_value,
            adj_close=bp_value,
            source="test",
        )
    )
    session.commit()


def test_uses_real_rates_when_present_in_bp_without_reconverting(db):
    """DB의 KTB1Y/KTB3Y는 이미 bp로 정규화돼 있다(ingest_macro_rates.py) —
    이 함수가 다시 ×100 하면 100배 오류가 조용히 생긴다(G13이 실제로
    노출되는 경로)."""
    as_of = date(2026, 7, 15)
    _seed_rate(db, "KTB1Y", as_of, 336.5)
    _seed_rate(db, "KTB3Y", as_of, 375.8)

    out = synthetic_city_ai_output(db, as_of)

    assert out["yield_1y_bp"] == 336.5
    assert out["yield_3y_bp"] == 375.8


def test_falls_back_to_synthetic_when_no_real_data(db):
    """백필 전 구간이나 자산이 아예 없으면 합성값으로 조용히 폴백해야 한다 —
    예외를 던지면 그 시점 리포트 전체가 죽는다."""
    as_of = date(2026, 7, 15)

    out = synthetic_city_ai_output(db, as_of)

    assert isinstance(out["yield_1y_bp"], float)
    assert isinstance(out["yield_3y_bp"], float)


def test_falls_back_when_db_is_none():
    """db=None(순수 계산 경로 테스트 등)이면 즉시 합성값을 쓴다 — DB 접근을
    시도하지 않는다."""
    out = synthetic_city_ai_output(None, date(2026, 7, 15))

    assert isinstance(out["yield_1y_bp"], float)
    assert isinstance(out["yield_3y_bp"], float)


def test_predicted_change_is_always_synthetic_and_deterministic(db):
    """predicted_change_bp는 아직 City AI 모델이 없어 실측/폴백 여부와 무관하게
    항상 같은 시드 기반 합성값이어야 한다 — 실측 금리커브 유무가 예측치 자체를
    바꾸면 안 된다(두 입력의 책임이 섞인다)."""
    as_of = date(2026, 7, 15)
    without_real_data = synthetic_city_ai_output(db, as_of)

    _seed_rate(db, "KTB1Y", as_of, 336.5)
    _seed_rate(db, "KTB3Y", as_of, 375.8)
    with_real_data = synthetic_city_ai_output(db, as_of)

    assert without_real_data["predicted_change_bp"] == with_real_data["predicted_change_bp"]


def test_partial_real_data_only_overrides_the_available_leg(db):
    """1년물만 실측이 있고 3년물이 없으면, 1년물만 실측을 쓰고 3년물은 폴백해야
    한다 — 한쪽이 없다고 둘 다 폴백으로 넘어가면 이미 있는 실측을 버리는 것이다."""
    as_of = date(2026, 7, 15)
    _seed_rate(db, "KTB1Y", as_of, 336.5)

    out = synthetic_city_ai_output(db, as_of)

    assert out["yield_1y_bp"] == 336.5
    assert isinstance(out["yield_3y_bp"], float)
    assert out["yield_3y_bp"] != 336.5


def test_respects_point_in_time_cutoff(db):
    """as_of보다 미래에 알려진(knowledge_date) 값은 보이면 안 된다 — MetroGuard의
    다른 모든 입력과 동일한 point-in-time 규율을 city_ai_stub도 지켜야 한다."""
    from datetime import timedelta

    trade_date = date(2026, 7, 15)
    asset = DimAsset(asset_type=AssetType.MACRO.value, code="KTB1Y", name_kr="KTB1Y", currency="KRW")
    db.add(asset)
    db.commit()
    db.refresh(asset)
    db.add(
        FactMarketDaily(
            asset_id=asset.asset_id,
            trade_date=trade_date,
            knowledge_date=trade_date + timedelta(days=30),
            close=336.5,
            adj_close=336.5,
            source="test",
        )
    )
    db.commit()

    out_before = synthetic_city_ai_output(db, trade_date)
    out_after = synthetic_city_ai_output(db, trade_date + timedelta(days=30))

    assert out_before["yield_1y_bp"] != 336.5  # 아직 안 보여야 하므로 폴백
    assert out_after["yield_1y_bp"] == 336.5
