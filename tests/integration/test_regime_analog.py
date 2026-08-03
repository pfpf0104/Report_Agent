"""역사적 레짐 패턴 매칭(analog.py) — 격리된 합성 이력으로 검증한다.

test_regime_classifier.py와 동일한 fixture 패턴(GROWTH_CODE/INFLATION_CODE에
_seed_monthly로 값을 심고 source="test"인 것만 정리)을 재사용한다 — 완전한
코드 격리는 불가능하지만(analog.py도 classifier.py의 상수 GROWTH_CODE/
INFLATION_CODE를 그대로 쓴다), 이 테스트가 심고 지우는 데이터로만 assert한다.

여기서 볼 것: 1) 이력 부족 시 보류 컨텍스트, 2) 매월 사분면 재구성이 실제로
classify_regime과 같은 판정을 내는가, 3) 현재와 같은 사분면인 과거 월만
골라내는가, 4) 최신월(현재 자신)은 매칭 결과에서 제외되는가.
"""
from datetime import date, timedelta

import pytest

from app.computation.regime.analog import (
    build_analog_report_context,
    build_quadrant_history,
    find_historical_analogs,
)
from app.computation.regime.classifier import GROWTH_CODE, INFLATION_CODE, MIN_MONTHS_FOR_TREND
from app.db.base import SessionLocal
from app.db.models.dim_asset import AssetType, DimAsset
from app.db.models.fact_market_daily import FactMarketDaily

CODES = [GROWTH_CODE, INFLATION_CODE]
# 운영 DB의 USINDPRO/USCPI는 2021-08부터 시작한다(Phase 0 백필 범위) — 그보다
# 훨씬 이전부터 심어야 합성 데이터가 실제 운영 데이터와 날짜가 겹치지 않는다.
START = date(1990, 1, 1)


def _cleanup(session):
    ids = session.query(DimAsset.asset_id).filter(DimAsset.code.in_(CODES))
    session.query(FactMarketDaily).filter(
        FactMarketDaily.asset_id.in_(ids), FactMarketDaily.source == "test"
    ).delete(synchronize_session=False)
    session.commit()


@pytest.fixture()
def db():
    session = SessionLocal()
    _cleanup(session)
    yield session
    _cleanup(session)
    session.close()


def _get_or_create_asset(session, code: str) -> DimAsset:
    asset = session.query(DimAsset).filter_by(code=code).first()
    if asset is not None:
        return asset
    asset = DimAsset(asset_type=AssetType.MACRO_ECONOMIC.value, code=code, name_kr=code, currency="USD")
    session.add(asset)
    session.commit()
    session.refresh(asset)
    return asset


def _monthly_dates(n: int, start: date = START) -> list[date]:
    dates = []
    year, month = start.year, start.month
    for _ in range(n):
        dates.append(date(year, month, 1))
        month += 1
        if month == 13:
            month = 1
            year += 1
    return dates


def _seed_monthly(session, code: str, values: list[float], *, start: date = START) -> None:
    asset = _get_or_create_asset(session, code)
    dates = _monthly_dates(len(values), start)
    session.bulk_save_objects([
        FactMarketDaily(
            asset_id=asset.asset_id, trade_date=d, knowledge_date=d,
            close=float(v), adj_close=float(v), source="test",
        )
        for d, v in zip(dates, values)
    ])
    session.commit()


def test_build_quadrant_history_returns_empty_when_insufficient_history(db):
    n = MIN_MONTHS_FOR_TREND - 1
    values = [100.0 + i for i in range(n)]
    _seed_monthly(db, GROWTH_CODE, values)
    _seed_monthly(db, INFLATION_CODE, values)

    as_of = _monthly_dates(n)[-1]
    history = build_quadrant_history(db, as_of)

    assert history == []


def test_build_quadrant_history_alternates_between_quadrants(db):
    """성장·인플레가 번갈아 가속/감속하도록 심어, 재구성된 사분면이 실제로
    바뀌는지 확인한다 — 매번 같은 사분면만 나오면 재구성 로직이 죽어 있는
    것이다."""
    n = 30
    # 처음 12개월은 base=100 평탄, 이후 반복 패턴으로 YoY가 오르내리게 한다.
    growth = [100.0] * 12 + [110.0, 90.0, 120.0, 80.0, 130.0, 70.0] * 3
    inflation = [100.0] * 12 + [105.0, 95.0, 115.0, 85.0, 125.0, 75.0] * 3
    growth = growth[:n]
    inflation = inflation[:n]
    _seed_monthly(db, GROWTH_CODE, growth)
    _seed_monthly(db, INFLATION_CODE, inflation)

    as_of = _monthly_dates(n)[-1]
    history = build_quadrant_history(db, as_of)

    quadrants_seen = {h.quadrant for h in history}
    assert len(quadrants_seen) > 1  # 재구성이 실제로 다양한 사분면을 만들어내야 한다


def test_find_historical_analogs_excludes_current_month_itself(db):
    n = MIN_MONTHS_FOR_TREND + 10
    values = [100.0 + i * 2 for i in range(n)]  # 꾸준히 가속하는 패턴
    _seed_monthly(db, GROWTH_CODE, values)
    _seed_monthly(db, INFLATION_CODE, values)

    as_of = _monthly_dates(n)[-1]
    ctx = find_historical_analogs(db, as_of)

    assert ctx.available is True
    current_month = _monthly_dates(n)[-1]
    for analog in ctx.analog_months:
        assert analog.month != current_month


def test_find_historical_analogs_only_returns_matching_quadrant(db):
    n = 30
    growth = [100.0] * 12 + [110.0, 90.0, 120.0, 80.0, 130.0, 70.0] * 3
    inflation = [100.0] * 12 + [105.0, 95.0, 115.0, 85.0, 125.0, 75.0] * 3
    growth = growth[:n]
    inflation = inflation[:n]
    _seed_monthly(db, GROWTH_CODE, growth)
    _seed_monthly(db, INFLATION_CODE, inflation)

    as_of = _monthly_dates(n)[-1]
    ctx = find_historical_analogs(db, as_of)

    assert ctx.available is True
    for analog in ctx.analog_months:
        assert analog.quadrant == ctx.current_quadrant


def test_find_historical_analogs_respects_point_in_time_cutoff(db):
    """as_of보다 미래에 알려진 값은 이력 재구성에 쓰이면 안 된다."""
    n = MIN_MONTHS_FOR_TREND + 10
    dates = _monthly_dates(n)
    values = [100.0 + i * 2 for i in range(n)]

    growth_asset = _get_or_create_asset(db, GROWTH_CODE)
    db.bulk_save_objects([
        FactMarketDaily(
            asset_id=growth_asset.asset_id, trade_date=d,
            knowledge_date=d + timedelta(days=400),
            close=v, adj_close=v, source="test",
        )
        for d, v in zip(dates, values)
    ])
    db.commit()
    _seed_monthly(db, INFLATION_CODE, values)

    ctx = find_historical_analogs(db, dates[-1])

    assert ctx.available is False


def test_build_analog_report_context_returns_pending_shape_when_unavailable(db):
    ctx = build_analog_report_context(db, date(2015, 1, 1))
    assert ctx["analog_available"] is False
    assert "analog_data_status" in ctx


def test_build_analog_report_context_returns_full_shape_when_available(db):
    n = MIN_MONTHS_FOR_TREND + 15
    growth = [100.0] * 12 + [110.0, 90.0, 120.0, 80.0, 130.0, 70.0, 110.0, 90.0, 120.0][:n - 12]
    inflation = [100.0] * 12 + [105.0, 95.0, 115.0, 85.0, 125.0, 75.0, 105.0, 95.0, 115.0][:n - 12]
    _seed_monthly(db, GROWTH_CODE, growth)
    _seed_monthly(db, INFLATION_CODE, inflation)

    as_of = _monthly_dates(n)[-1]
    ctx = build_analog_report_context(db, as_of)

    assert ctx["analog_available"] is True
    assert "analog_current_quadrant" in ctx
    assert "analog_months_rows" in ctx
    assert "analog_disclosure" in ctx
    assert "예측하지 않는다" in ctx["analog_disclosure"]
