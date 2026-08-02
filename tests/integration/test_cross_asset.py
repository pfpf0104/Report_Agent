"""크로스에셋 상관행렬 — 실제 로컬 Postgres에 가격 이력을 넣고 검증한다.

MASTER_PLAN Phase 3-2. 여기서 볼 것은 세 가지다.
  1) 이력이 부족하면 숫자 대신 보류 컨텍스트가 나오는가.
  2) 상관계수가 손으로 굴린 값(np.corrcoef)과 일치하는가.
  3) 대각선이 항상 1.0인가(자기 자신과의 상관).
"""
from datetime import date, timedelta

import numpy as np
import pytest

from app.computation.risk.cross_asset import (
    _default_representative_assets,
    build_cross_asset_correlation,
    build_cross_asset_report_context,
)
from app.computation.risk.report_context import MIN_BACKTEST_OBSERVATIONS
from app.db.base import SessionLocal
from app.db.models.dim_asset import AssetType, DimAsset
from app.db.models.fact_market_daily import FactMarketDaily

CODES = ["_XA_1", "_XA_2", "_XA_3"]
START = date(2021, 1, 4)


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


def _business_days(n: int, start: date = START) -> list[date]:
    days, d = [], start
    while len(days) < n:
        if d.weekday() < 5:
            days.append(d)
        d += timedelta(days=1)
    return days


def _seed(session, code: str, dates: list[date], prices) -> None:
    asset = DimAsset(asset_type=AssetType.ETF.value, code=code, name_kr=code, currency="USD")
    session.add(asset)
    session.commit()
    session.refresh(asset)
    session.bulk_save_objects([
        FactMarketDaily(
            asset_id=asset.asset_id, trade_date=d, knowledge_date=d,
            close=float(p), adj_close=float(p), source="test",
        )
        for d, p in zip(dates, prices)
    ])
    session.commit()


def _price_path(n: int, seed: int, drift: float = 0.0003, vol: float = 0.012):
    rng = np.random.default_rng(seed)
    returns = rng.normal(drift, vol, size=n - 1)
    return 100.0 * np.concatenate([[1.0], np.cumprod(1.0 + returns)])


def test_returns_pending_when_no_data(db):
    ctx = build_cross_asset_correlation(
        db, date(2026, 8, 1), {c: c for c in CODES}
    )
    assert ctx.available is False
    assert "2개 이상" in ctx.data_status


def test_returns_pending_when_only_one_asset_has_data(db):
    n = MIN_BACKTEST_OBSERVATIONS + 10
    dates = _business_days(n)
    _seed(db, CODES[0], dates, _price_path(n, 1))

    ctx = build_cross_asset_correlation(db, dates[-1], {c: c for c in CODES})

    assert ctx.available is False


def test_returns_pending_when_history_too_short(db):
    n = 30
    dates = _business_days(n)
    for i, code in enumerate(CODES):
        _seed(db, code, dates, _price_path(n, 10 + i))

    ctx = build_cross_asset_correlation(db, dates[-1], {c: c for c in CODES})

    assert ctx.available is False
    assert str(MIN_BACKTEST_OBSERVATIONS) in ctx.data_status


def test_correlation_matches_manual_corrcoef(db):
    n = MIN_BACKTEST_OBSERVATIONS + 300
    dates = _business_days(n)
    prices = {}
    for i, code in enumerate(CODES):
        p = _price_path(n, 100 + i, vol=0.01 + 0.005 * i)
        prices[code] = p
        _seed(db, code, dates, p)

    ctx = build_cross_asset_correlation(db, dates[-1], {c: c for c in CODES})

    assert ctx.available is True
    returns = np.column_stack(
        [np.diff(prices[c]) / prices[c][:-1] for c in ctx.codes]
    )
    expected = np.corrcoef(returns, rowvar=False)
    actual = np.array(ctx.correlation)
    # fact_market_daily.close/adj_close는 Numeric(18,4)라 DB 왕복 시 소수점
    # 4자리로 반올림된다 — float 그대로 굴린 손계산과는 그만큼 오차가 생긴다.
    # 완전 무결성이 아니라 "같은 계산식을 쓰고 있는가"를 확인하는 것이 목적이라
    # 반올림 오차 수준(1e-3)으로 허용한다.
    np.testing.assert_allclose(actual, expected, atol=1e-3)


def test_diagonal_is_always_one(db):
    n = MIN_BACKTEST_OBSERVATIONS + 300
    dates = _business_days(n)
    for i, code in enumerate(CODES):
        _seed(db, code, dates, _price_path(n, 200 + i))

    ctx = build_cross_asset_correlation(db, dates[-1], {c: c for c in CODES})

    for i in range(len(ctx.codes)):
        assert ctx.correlation[i][i] == pytest.approx(1.0)


def test_correlation_is_symmetric(db):
    n = MIN_BACKTEST_OBSERVATIONS + 300
    dates = _business_days(n)
    for i, code in enumerate(CODES):
        _seed(db, code, dates, _price_path(n, 300 + i, vol=0.008 + 0.006 * i))

    ctx = build_cross_asset_correlation(db, dates[-1], {c: c for c in CODES})

    matrix = np.array(ctx.correlation)
    np.testing.assert_allclose(matrix, matrix.T, atol=1e-12)


def test_default_representative_assets_stays_in_sync_with_each_report(db):
    """대표 자산 코드는 각 리포트 모듈이 실제로 쓰는 벤치마크 상수를 직접
    참조해야 한다 — 문자열로 다시 하드코딩하면 리포트 쪽 벤치마크가 바뀌었을
    때 이 페이지가 조용히 낡은 자산을 계속 보여준다."""
    from app.computation.quant.ridge_sector_rank import PERFORMANCE_BENCHMARK
    from app.computation.valuation.residual_income_model import STOCK_CODE
    from app.ingestion.jobs.ingest_korean_equity_prices import BOND_ETF_LONG

    codes = set(_default_representative_assets().keys())

    assert codes == {PERFORMANCE_BENCHMARK, BOND_ETF_LONG, STOCK_CODE["삼성전자"]}


def test_report_context_returns_pending_shape_when_unavailable(db):
    """기본 대표 자산(SPY/114260/005930)은 실제 운영 DB에 이미 데이터가 있어
    이 테스트로는 보류 상태를 재현할 수 없다 — asset_codes로 존재하지 않는
    코드를 대신 넣어 이력 없음 경로를 확인한다."""
    ctx = build_cross_asset_report_context(
        db, date(2026, 8, 1), asset_codes={c: c for c in CODES}
    )

    assert ctx["cross_asset_available"] is False
    assert "cross_asset_data_status" in ctx
    assert "cross_asset_table_rows" not in ctx


def test_report_context_returns_full_shape_when_available(db):
    n = MIN_BACKTEST_OBSERVATIONS + 300
    dates = _business_days(n)
    for i, code in enumerate(CODES):
        _seed(db, code, dates, _price_path(n, 400 + i))

    ctx = build_cross_asset_report_context(db, dates[-1], asset_codes={c: c for c in CODES})

    assert ctx["cross_asset_available"] is True
    assert len(ctx["cross_asset_table_rows"]) == len(CODES)
    assert all(len(row) == len(CODES) + 1 for row in ctx["cross_asset_table_rows"])
    assert ctx["cross_asset_heatmap_chart_uri"].startswith("data:image/png;base64,")
    assert "레짐" in ctx["cross_asset_disclosure"]
    # 이 페이지가 다루는 자산이 몇 개뿐인지(전체 크로스에셋 분석이 아니라는
    # 사실)를 명시해야 한다 — "CROSS-ASSET VIEW"라는 제목만 보고 독자가
    # 포괄적인 분석으로 오인하지 않게.
    assert str(len(CODES)) in ctx["cross_asset_disclosure"]
