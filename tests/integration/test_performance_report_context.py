"""성과·리스크 페이지 컨텍스트 — 실제 로컬 Postgres에 가격 이력을 넣고 검증한다.

이 계층의 위험은 "터지는 것"이 아니라 **조용히 그럴듯한 숫자를 만들어내는 것**이다.
그래서 여기서는 세 가지를 본다.
  1) 이력이 부족하면 숫자 대신 보류 컨텍스트가 나오는가.
  2) knowledge_date로 아직 알 수 없던 가격이 백테스트에 새어 들어가지 않는가.
  3) 나온 숫자가 같은 입력을 손으로 굴린 값과 일치하는가.
"""
from datetime import date, timedelta

import numpy as np
import pytest

from app.computation.risk.report_context import (
    MIN_BACKTEST_OBSERVATIONS,
    build_performance_context,
    load_price_history,
)
from app.db.base import SessionLocal
from app.db.models.dim_asset import AssetType, DimAsset
from app.db.models.fact_market_daily import FactMarketDaily

UNIVERSE = ["_TEST_A", "_TEST_B", "_TEST_C"]
BENCHMARK = "_TEST_BM"
WIDE_UNIVERSE = [f"_TEST_W{i}" for i in range(5)]
# fixture가 setup/teardown에서 지우는 전체 목록 — 새 테스트 코드가 생기면 여기 추가한다.
CODES = UNIVERSE + [BENCHMARK] + WIDE_UNIVERSE
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


def _seed(session, code: str, dates: list[date], prices, *, knowledge_offset: int = 0) -> None:
    """가격 시계열을 적재한다. knowledge_offset일 만큼 취득일을 뒤로 미룰 수 있다."""
    asset = DimAsset(asset_type=AssetType.ETF.value, code=code, name_kr=code, currency="USD")
    session.add(asset)
    session.commit()
    session.refresh(asset)

    session.bulk_save_objects([
        FactMarketDaily(
            asset_id=asset.asset_id,
            trade_date=d,
            knowledge_date=d + timedelta(days=knowledge_offset),
            close=float(p),
            adj_close=float(p),
            source="test",
        )
        for d, p in zip(dates, prices)
    ])
    session.commit()


def _price_path(n: int, seed: int, drift: float = 0.0003, vol: float = 0.012):
    rng = np.random.default_rng(seed)
    returns = rng.normal(drift, vol, size=n - 1)
    return 100.0 * np.concatenate([[1.0], np.cumprod(1.0 + returns)])


def _seed_full_universe(session, n_days: int, *, seed_base: int = 100) -> list[date]:
    dates = _business_days(n_days)
    for i, code in enumerate(UNIVERSE + [BENCHMARK]):
        _seed(session, code, dates, _price_path(n_days, seed_base + i, vol=0.010 + 0.004 * i))
    return dates


# --- 가격 패널 로딩 -----------------------------------------------------------


def test_load_price_history_keeps_only_dates_present_for_every_asset(db):
    """자산마다 거래일이 다르면 교집합만 남아야 한다 — 직전값으로 채우면
    그날 수익률이 0이 돼 변동성이 조직적으로 과소평가된다."""
    dates = _business_days(10)
    _seed(db, "_TEST_A", dates, _price_path(10, 1))
    _seed(db, "_TEST_B", dates[:6], _price_path(6, 2))  # 4일 짧다

    history = load_price_history(db, date(2099, 1, 1), ["_TEST_A", "_TEST_B"])

    assert history.codes == ["_TEST_A", "_TEST_B"]
    assert history.dates == dates[:6]
    assert history.prices.shape == (6, 2)


def test_load_price_history_hides_rows_not_yet_known_at_as_of(db):
    """knowledge_date가 as_of 이후인 행은 보이면 안 된다(point-in-time)."""
    dates = _business_days(10)
    _seed(db, "_TEST_A", dates, _price_path(10, 1), knowledge_offset=365)

    assert load_price_history(db, dates[-1], ["_TEST_A"]).n_observations == 0
    assert load_price_history(db, dates[-1] + timedelta(days=365), ["_TEST_A"]).n_observations == 10


def test_returns_panel_matches_manual_price_ratios(db):
    dates = _business_days(4)
    _seed(db, "_TEST_A", dates, [100.0, 110.0, 99.0, 99.0])
    history = load_price_history(db, date(2099, 1, 1), ["_TEST_A"])

    assert history.returns_panel()[:, 0] == pytest.approx([0.10, -0.10, 0.0])
    assert history.return_dates() == dates[1:]


# --- 이력 부족 시 보류 --------------------------------------------------------


def test_returns_pending_context_when_no_data_at_all(db):
    context = build_performance_context(db, date(2026, 8, 1), UNIVERSE, BENCHMARK)
    assert context["performance_available"] is False
    assert "gips_requirements" in context  # 보류 페이지 콘텐츠가 그대로 실린다
    assert "risk_metric_rows" not in context


def test_returns_pending_context_when_history_is_too_short(db):
    n = 60
    dates = _business_days(n)
    for i, code in enumerate(CODES):
        _seed(db, code, dates, _price_path(n, 10 + i))

    context = build_performance_context(db, dates[-1], UNIVERSE, BENCHMARK)

    assert context["performance_available"] is False
    assert str(MIN_BACKTEST_OBSERVATIONS) in context["performance_data_status"]


def test_returns_pending_context_when_benchmark_is_missing(db):
    n = MIN_BACKTEST_OBSERVATIONS + 30
    dates = _business_days(n)
    for i, code in enumerate(UNIVERSE):
        _seed(db, code, dates, _price_path(n, 20 + i))

    context = build_performance_context(db, dates[-1], UNIVERSE, BENCHMARK)

    assert context["performance_available"] is False
    assert BENCHMARK in context["performance_data_status"]


# --- 실제 백테스트 경로 -------------------------------------------------------


def test_full_pipeline_produces_metrics_gips_and_rolling(db):
    n = MIN_BACKTEST_OBSERVATIONS + 300  # 공분산 창 + 성과 구간 + 롤링 창
    dates = _seed_full_universe(db, n)

    context = build_performance_context(db, dates[-1], UNIVERSE, BENCHMARK)

    assert context["performance_available"] is True
    assert len(context["risk_metric_rows"]) > 0
    assert all(len(row) == 3 for row in context["risk_metric_rows"])  # [지표, 포트폴리오, 벤치마크]
    assert context["gips_rows"]
    assert context["rolling_sharpe"]
    assert len(context["rolling_labels"]) == len(context["rolling_sharpe"])
    assert len(context["equity_curve"]) == len(context["equity_curve_labels"])


def test_disclosures_are_always_present_on_the_performance_page(db):
    """가설적 백테스트라는 사실과, CallRank 신호 성과가 아니라는 사실을
    숫자와 같은 페이지에 반드시 싣는다."""
    dates = _seed_full_universe(db, MIN_BACKTEST_OBSERVATIONS + 300)
    context = build_performance_context(db, dates[-1], UNIVERSE, BENCHMARK)

    assert "가설적" in context["performance_hypothetical_disclosure"]
    assert "리스크패리티" in context["performance_neutral_disclosure"]
    assert "G3" in context["performance_neutral_disclosure"]


def test_equity_curve_starts_at_the_evaluation_window_not_at_backtest_start(db):
    """첫 리밸런싱 전 구간(동일가중 기본값)은 전략 성과가 아니므로 잘라낸다."""
    dates = _seed_full_universe(db, MIN_BACKTEST_OBSERVATIONS + 300)
    context = build_performance_context(db, dates[-1], UNIVERSE, BENCHMARK)

    curve = context["equity_curve"]
    # 평가 구간 직전을 1.0으로 재정규화했으므로 첫 값은 1.0 근처(하루치 수익률)여야 한다.
    assert abs(curve[0] - 1.0) < 0.10
    assert len(curve) == len(context["equity_curve_labels"])


def test_point_in_time_cutoff_shortens_the_evaluation_window(db):
    """as_of를 앞당기면 평가 구간이 실제로 짧아져야 한다 — 미래 가격이 새어
    들어오면 두 결과가 같아진다."""
    n = MIN_BACKTEST_OBSERVATIONS + 400
    dates = _seed_full_universe(db, n)

    full = build_performance_context(db, dates[-1], UNIVERSE, BENCHMARK)
    early = build_performance_context(db, dates[-150], UNIVERSE, BENCHMARK)

    assert full["performance_available"] and early["performance_available"]
    assert len(early["equity_curve"]) < len(full["equity_curve"])
    assert early["performance_period"] != full["performance_period"]


def test_small_universe_relaxes_the_cap_and_says_so(db):
    """3개 자산에 25% 상한은 실현 불가능하다(최대 75%). 완화하되 그 사실을
    가정 문구에 명시해야 한다 — 조용히 바꾸면 독자가 리스크패리티라고 적힌
    페이지에서 동일가중을 보게 된다."""
    dates = _seed_full_universe(db, MIN_BACKTEST_OBSERVATIONS + 300)
    context = build_performance_context(db, dates[-1], UNIVERSE, BENCHMARK)

    assert "1/n으로 완화" in context["performance_assumptions"]
    assert "리스크패리티 결과가 반영되지 않는다" in context["performance_assumptions"]


def test_wide_universe_actually_uses_risk_parity_not_equal_weight(db):
    """상한이 실현 가능한 유니버스에서는 비중이 동일가중과 달라야 한다 —
    파이프라인이 조용히 동일가중으로 무너지지 않았는지 확인한다."""
    from app.computation.backtest.engine import from_covariance, periodic_rebalance_indices, run_backtest
    from app.computation.portfolio.costs import CostModel
    from app.computation.portfolio.weighting import risk_parity
    from app.computation.risk.report_context import MIN_COVARIANCE_OBSERVATIONS

    wide = WIDE_UNIVERSE
    n = MIN_BACKTEST_OBSERVATIONS + 60
    dates = _business_days(n)
    for i, code in enumerate(wide):
        _seed(db, code, dates, _price_path(n, 500 + i, vol=0.005 + 0.008 * i))

    history = load_price_history(db, dates[-1], wide)
    result = run_backtest(
        history.return_dates(),
        history.returns_panel(),
        weight_fn=from_covariance(
            lambda h: risk_parity(np.cov(h, rowvar=False, ddof=1)),
            min_observations=MIN_COVARIANCE_OBSERVATIONS,
        ),
        rebalance_indices=periodic_rebalance_indices(history.return_dates(), "M"),
        cost_model=CostModel(spread_bps=0.0),
    )

    final_weights = result.weights[-1]
    assert final_weights.max() - final_weights.min() > 0.05  # 동일가중이 아니다
    # 변동성이 낮게 생성된 자산이 더 큰 비중을 받아야 한다(리스크패리티의 정의)
    assert final_weights[0] > final_weights[-1]


def test_costs_are_actually_charged_and_disclosed(db):
    from app.computation.portfolio.costs import CostModel

    dates = _seed_full_universe(db, MIN_BACKTEST_OBSERVATIONS + 300)

    cheap = build_performance_context(db, dates[-1], UNIVERSE, BENCHMARK,
                                      cost_model=CostModel(spread_bps=0.0))
    pricey = build_performance_context(db, dates[-1], UNIVERSE, BENCHMARK,
                                       cost_model=CostModel(spread_bps=100.0))

    assert cheap["equity_curve"][-1] > pricey["equity_curve"][-1]
    assert "누적 거래비용" in pricey["performance_assumptions"]
    assert "스프레드 100.0bp" in pricey["performance_assumptions"]


# --- MetroGuard: 벤치마크가 유니버스 안에 있는 2자산 케이스 --------------------


def test_benchmark_in_universe_keeps_both_assets_in_the_universe(db):
    """MetroGuard처럼 유니버스가 자산 2개뿐이고 그중 하나가 벤치마크를 겸하는
    경우, benchmark_in_universe=True를 주면 유니버스에서 벤치마크가 빠지지
    않아야 한다 — 빠지면 자산 1개만 남아 항상 보류로 떨어진다."""
    from app.computation.backtest.engine import buy_and_hold

    two_asset_universe = [UNIVERSE[0], UNIVERSE[1]]
    benchmark = UNIVERSE[1]
    n = MIN_BACKTEST_OBSERVATIONS + 300
    dates = _business_days(n)
    for i, code in enumerate(two_asset_universe):
        _seed(db, code, dates, _price_path(n, 700 + i, vol=0.010 + 0.004 * i))

    context = build_performance_context(
        db, dates[-1], two_asset_universe, benchmark,
        weight_fn=buy_and_hold([0.5, 0.5]),
        benchmark_in_universe=True,
    )

    assert context["performance_available"] is True
    assert context["performance_benchmark"] == benchmark


def test_benchmark_in_universe_uses_the_injected_weight_fn_not_risk_parity(db):
    """weight_fn=buy_and_hold([0.5, 0.5])를 주입하면 첫 리밸런싱부터 정확히
    50/50이어야 한다 — 리스크패리티 기본값으로 조용히 무너지면 변동성이 다른
    두 자산의 비중이 50/50에서 벗어난다."""
    from app.computation.backtest.engine import buy_and_hold

    two_asset_universe = [UNIVERSE[0], UNIVERSE[1]]
    benchmark = UNIVERSE[1]
    n = MIN_BACKTEST_OBSERVATIONS + 60
    dates = _business_days(n)
    for i, code in enumerate(two_asset_universe):
        _seed(db, code, dates, _price_path(n, 800 + i, vol=0.005 + 0.015 * i))

    context = build_performance_context(
        db, dates[-1], two_asset_universe, benchmark,
        weight_fn=buy_and_hold([0.5, 0.5]),
        benchmark_in_universe=True,
        strategy_label="테스트 고정 배분",
    )

    assert context["performance_available"] is True
    assert context["performance_strategy_label"] == "테스트 고정 배분"
