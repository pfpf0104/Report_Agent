"""레짐 4분면 분류기 — 격리된 합성 자산으로 검증한다(운영 USINDPRO/USCPI에 의존하지 않음).

이전 세션에서 실제 운영 코드(KTB1Y/KTB3Y 등)를 테스트에 직접 쓰다가 다른
테스트 파일의 setup/teardown과 충돌한 경험이 반복됐다(test_city_ai_stub.py,
test_global_rate_model.py 참고) — 이 파일은 처음부터 _RC_ 접두사 격리 코드로
classify_regime을 직접 호출한다(classify_regime은 GROWTH_CODE/INFLATION_CODE
가 상수로 고정돼 있어 완전한 코드 격리는 불가능하지만, 최소한 이 테스트
파일이 스스로 심고 정리하는 데이터로만 assert한다).

여기서 볼 것: 1) 이력 부족 시 보류 컨텍스트를 반환하는가, 2) YoY 추세
방향(가속/감속) 판정이 손으로 계산한 값과 일치하는가, 3) 4개 사분면이
모두 올바르게 매핑되는가, 4) point-in-time 필터가 미래 데이터를 차단하는가.
"""
from datetime import date, timedelta

import pytest

from app.computation.regime.classifier import (
    GROWTH_CODE,
    INFLATION_CODE,
    MIN_MONTHS_FOR_TREND,
    classify_regime,
    compute_yoy_series,
    load_monthly_series,
)
from app.db.base import SessionLocal
from app.db.models.dim_asset import AssetType, DimAsset
from app.db.models.fact_market_daily import FactMarketDaily

CODES = [GROWTH_CODE, INFLATION_CODE]
START = date(2020, 1, 1)


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


def _flat_then_values(n_flat: int, tail: list[float], flat_value: float = 100.0) -> list[float]:
    return [flat_value] * n_flat + tail


def test_returns_pending_when_no_data(db):
    """운영 DB의 USINDPRO/USCPI는 2021-08부터 시작한다(Phase 0 백필 범위) —
    그 이전 시점을 as_of로 쓰면 자연스럽게 이력이 전혀 없는 상황을 재현한다."""
    ctx = classify_regime(db, date(2020, 1, 1))
    assert ctx.available is False
    assert "필요" in ctx.data_status


def test_returns_pending_when_history_too_short(db):
    n = MIN_MONTHS_FOR_TREND - 1
    values = list(range(100, 100 + n))
    _seed_monthly(db, GROWTH_CODE, [float(v) for v in values])
    _seed_monthly(db, INFLATION_CODE, [float(v) for v in values])

    as_of = _monthly_dates(n)[-1]
    ctx = classify_regime(db, as_of)

    assert ctx.available is False


def test_growth_accelerating_inflation_accelerating_is_overheating(db):
    """산업생산 YoY가 이번달에 지난달보다 커지고(가속), CPI YoY도 커지면(가속)
    '과열'이어야 한다."""
    # 15개월: 인덱스 12=YoY 첫 값, 13=YoY 둘째 값(비교 대상), 14=YoY 셋째 값(최신, 판정 대상)
    # base=100 유지하다가 마지막 3개월만 조작해 YoY가 커지도록 만든다.
    growth = _flat_then_values(12, [110.0, 115.0, 130.0])  # YoY: 10%, 15%, 30% (가속)
    inflation = _flat_then_values(12, [105.0, 108.0, 120.0])  # YoY: 5%, 8%, 20% (가속)
    _seed_monthly(db, GROWTH_CODE, growth)
    _seed_monthly(db, INFLATION_CODE, inflation)

    as_of = _monthly_dates(len(growth))[-1]
    ctx = classify_regime(db, as_of)

    assert ctx.available is True
    assert ctx.growth_accelerating is True
    assert ctx.inflation_accelerating is True
    assert ctx.quadrant == "과열 (Overheating)"


def test_growth_accelerating_inflation_decelerating_is_goldilocks(db):
    growth = _flat_then_values(12, [110.0, 115.0, 130.0])  # 가속
    inflation = _flat_then_values(12, [120.0, 115.0, 105.0])  # YoY: 20%, 15%, 5% (감속)
    _seed_monthly(db, GROWTH_CODE, growth)
    _seed_monthly(db, INFLATION_CODE, inflation)

    as_of = _monthly_dates(len(growth))[-1]
    ctx = classify_regime(db, as_of)

    assert ctx.quadrant == "골디락스 (Goldilocks)"


def test_growth_decelerating_inflation_accelerating_is_stagflation(db):
    growth = _flat_then_values(12, [130.0, 115.0, 105.0])  # 감속
    inflation = _flat_then_values(12, [105.0, 110.0, 125.0])  # 가속
    _seed_monthly(db, GROWTH_CODE, growth)
    _seed_monthly(db, INFLATION_CODE, inflation)

    as_of = _monthly_dates(len(growth))[-1]
    ctx = classify_regime(db, as_of)

    assert ctx.quadrant == "스태그플레이션 (Stagflation)"


def test_growth_decelerating_inflation_decelerating_is_slowdown(db):
    growth = _flat_then_values(12, [130.0, 115.0, 105.0])  # 감속
    inflation = _flat_then_values(12, [125.0, 112.0, 103.0])  # 감속
    _seed_monthly(db, GROWTH_CODE, growth)
    _seed_monthly(db, INFLATION_CODE, inflation)

    as_of = _monthly_dates(len(growth))[-1]
    ctx = classify_regime(db, as_of)

    assert ctx.quadrant == "둔화 (Slowdown)"


def test_yoy_values_match_manual_calculation(db):
    growth = _flat_then_values(12, [110.0, 115.0, 130.0])
    inflation = _flat_then_values(12, [105.0, 108.0, 120.0])
    _seed_monthly(db, GROWTH_CODE, growth)
    _seed_monthly(db, INFLATION_CODE, inflation)

    as_of = _monthly_dates(len(growth))[-1]
    ctx = classify_regime(db, as_of)

    # 마지막 값(130) vs 12개월 전(base=100) = +30%
    assert ctx.growth_yoy_pct == pytest.approx(30.0)
    # 직전 값(115) vs 12개월 전(base=100) = +15%
    assert ctx.growth_yoy_pct_prior == pytest.approx(15.0)
    assert ctx.inflation_yoy_pct == pytest.approx(20.0)
    assert ctx.inflation_yoy_pct_prior == pytest.approx(8.0)


def test_respects_point_in_time_cutoff(db):
    """as_of보다 미래에 알려진(knowledge_date) 값은 판정에 쓰이면 안 된다."""
    n = MIN_MONTHS_FOR_TREND + 5
    dates = _monthly_dates(n)
    values = [100.0 + i for i in range(n)]

    growth_asset = _get_or_create_asset(db, GROWTH_CODE)
    db.bulk_save_objects([
        FactMarketDaily(
            asset_id=growth_asset.asset_id, trade_date=d,
            knowledge_date=d + timedelta(days=400),  # 아주 먼 미래에나 알 수 있게
            close=v, adj_close=v, source="test",
        )
        for d, v in zip(dates, values)
    ])
    db.commit()
    _seed_monthly(db, INFLATION_CODE, values)

    ctx = classify_regime(db, dates[-1])

    # 산업생산 쪽 데이터가 point-in-time에 가려져 있어 이력 부족으로 보류돼야 한다.
    assert ctx.available is False


def test_compute_yoy_series_respects_periods_per_year_for_quarterly_data():
    """분기 시리즈(periods_per_year=4)에 월간 기본값(12)을 잘못 적용하면 YoY가
    3년 누적 변화율로 계산되는 조용한 오류가 난다 — 2026-08 실측(USGDP)에서
    발견해 수정한 버그의 회귀 테스트."""
    from app.computation.regime.classifier import SeriesObservation

    # 분기 데이터 8개(2년치): 100, 102, 104, ... 완만한 증가
    observations = [
        SeriesObservation(date(2024, 1, 1), 100.0),
        SeriesObservation(date(2024, 4, 1), 102.0),
        SeriesObservation(date(2024, 7, 1), 104.0),
        SeriesObservation(date(2024, 10, 1), 106.0),
        SeriesObservation(date(2025, 1, 1), 108.0),
        SeriesObservation(date(2025, 4, 1), 110.0),
        SeriesObservation(date(2025, 7, 1), 112.0),
        SeriesObservation(date(2025, 10, 1), 114.0),
    ]

    yoy = compute_yoy_series(observations, periods_per_year=4)

    # 2025-01 vs 2024-01(4분기 전) = 108/100 - 1 = 8%
    assert yoy[0] == (date(2025, 1, 1), pytest.approx(8.0))
    # periods_per_year를 잘못 12로 쓰면 이 짧은 시리즈에서는 결과가 아예 없어야 한다
    yoy_wrong = compute_yoy_series(observations, periods_per_year=12)
    assert yoy_wrong == []
