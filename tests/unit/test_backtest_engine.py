"""백테스트 엔진 검증.

이 프로젝트는 하드코딩된 가짜 백테스트 성과를 리포트에 실었던 전례가 있다
(MASTER_PLAN G2). 그래서 여기서는 "돌아간다"가 아니라 **손으로 검산 가능한
값과 정확히 일치하는가**, 그리고 **룩어헤드가 구조적으로 불가능한가**를 본다.
"""
from datetime import date, timedelta

import numpy as np
import pytest

from app.computation.backtest.engine import (
    BacktestResult,
    buy_and_hold,
    from_covariance,
    periodic_rebalance_indices,
    run_backtest,
)
from app.computation.portfolio.constraints import ConstraintSet
from app.computation.portfolio.costs import CostModel

FREE = CostModel(spread_bps=0.0)


def _dates(n: int, start: date = date(2024, 1, 1)) -> list[date]:
    return [start + timedelta(days=i) for i in range(n)]


# --- 기본 회계 ---------------------------------------------------------------


def test_single_asset_returns_match_panel_exactly():
    panel = np.array([[0.01], [-0.02], [0.03]])
    result = run_backtest(
        _dates(3), panel, weight_fn=buy_and_hold([1.0]), rebalance_indices=[0], cost_model=FREE
    )
    assert result.returns == pytest.approx([0.01, -0.02, 0.03])
    assert result.total_cost == pytest.approx(0.0)


def test_equity_curve_compounds_and_starts_at_one():
    panel = np.array([[0.10], [0.10]])
    result = run_backtest(
        _dates(2), panel, weight_fn=buy_and_hold([1.0]), rebalance_indices=[0], cost_model=FREE
    )
    # 1.0 → 1.1 → 1.21 (산술합 1.20이 아니다)
    assert result.equity_curve == pytest.approx([1.0, 1.1, 1.21])


def test_default_initial_weights_are_equal_weight():
    panel = np.array([[0.10, 0.00]])
    result = run_backtest(_dates(1), panel, weight_fn=buy_and_hold([1.0, 0.0]),
                          rebalance_indices=[], cost_model=FREE)
    assert result.returns[0] == pytest.approx(0.05)
    assert result.weights[0] == pytest.approx([0.5, 0.5])


# --- 룩어헤드 방지 -----------------------------------------------------------


def test_weight_fn_never_sees_the_period_it_decides_for():
    seen: list[int] = []

    def spy(t, history):
        seen.append(len(history))
        return [1.0]

    panel = np.array([[0.01]] * 10)
    run_backtest(_dates(10), panel, weight_fn=spy, rebalance_indices=[0, 3, 7], cost_model=FREE)

    # t기 비중을 정할 때 볼 수 있는 행 수는 정확히 t개(= 0..t-1기) 여야 한다.
    assert seen == [0, 3, 7]


def test_future_rows_are_absent_not_merely_off_limits():
    """미래를 '보지 말라'는 규율이 아니라, 배열에 존재하지 않아서 못 보는 것이다."""

    def peeker(t, history):
        return [float(history[t][0])]  # t기 행에 접근 시도 — 슬라이스에 없다

    panel = np.array([[0.01]] * 5)
    with pytest.raises(IndexError):
        run_backtest(_dates(5), panel, weight_fn=peeker, rebalance_indices=[2], cost_model=FREE)


def test_perfect_foresight_strategy_cannot_be_expressed():
    """마지막으로 오른 자산에 몰아주는 전략은 '직전 기간' 기준일 뿐,
    당기 승자를 맞힐 수 없다 — 실제로 완전예지 수익률에 못 미친다."""
    panel = np.array([[0.10, -0.10], [-0.10, 0.10], [0.10, -0.10], [-0.10, 0.10]])

    def chase_last_winner(t, history):
        if len(history) == 0:
            return None
        return [1.0, 0.0] if history[-1][0] > history[-1][1] else [0.0, 1.0]

    result = run_backtest(
        _dates(4), panel, weight_fn=chase_last_winner,
        rebalance_indices=[0, 1, 2, 3], cost_model=FREE,
    )
    perfect = float(np.prod(1.0 + panel.max(axis=1)) - 1.0)
    achieved = float(np.prod(1.0 + result.returns) - 1.0)
    assert achieved < perfect
    # 이 반전 시계열에서는 직전 승자 추종이 매번 틀린다(t=1 이후 전부 -10%).
    assert result.returns[1:] == pytest.approx([-0.10, -0.10, -0.10])


# --- 비중 드리프트 -----------------------------------------------------------


def test_weights_drift_between_rebalances():
    # t=0에 50/50, A가 +100% → 기말 비중 (2/3, 1/3). t=1 수익은 그 비중으로 난다.
    panel = np.array([[1.00, 0.00], [0.10, 0.00]])
    result = run_backtest(
        _dates(2), panel, weight_fn=buy_and_hold([0.5, 0.5]),
        rebalance_indices=[0], cost_model=FREE,
    )
    assert result.returns[0] == pytest.approx(0.50)
    # 드리프트를 무시하면 0.05가 나온다 — 그 값이면 이 테스트는 실패해야 한다.
    assert result.returns[1] == pytest.approx(2 / 3 * 0.10)


def test_turnover_is_measured_against_drifted_weights_not_last_target():
    """리밸런싱 비용의 기준은 '직전 목표비중'이 아니라 '지금 실제 비중'이다.
    이걸 틀리면 거래량이 과소평가돼 성과가 부풀려진다."""
    panel = np.array([[1.00, 0.00], [0.00, 0.00]])
    result = run_backtest(
        _dates(2), panel, weight_fn=buy_and_hold([0.5, 0.5]),
        rebalance_indices=[0, 1], cost_model=FREE,
    )
    assert result.turnovers[0] == pytest.approx(0.0)  # 초기 동일비중 = 목표
    # 드리프트된 (2/3, 1/3)을 (0.5, 0.5)로 되돌리는 단방향 회전율
    assert result.turnovers[1] == pytest.approx(1 / 6)


# --- 거래비용 ---------------------------------------------------------------


def test_cost_is_deducted_at_the_rebalancing_period():
    model = CostModel(spread_bps=10.0)  # 편도 5bp
    panel = np.array([[0.00, 0.00], [0.00, 0.00]])
    result = run_backtest(
        _dates(2), panel, weight_fn=buy_and_hold([0.0, 1.0]),
        rebalance_indices=[0], cost_model=model, initial_weights=[1.0, 0.0],
    )
    # Σ|Δw| = 2, 편도 5bp → 총 10bp
    assert result.costs[0] == pytest.approx(0.0010)
    assert result.costs[1] == pytest.approx(0.0)
    assert result.returns[0] == pytest.approx(-0.0010)
    assert result.gross_returns[0] == pytest.approx(0.0)
    assert result.total_cost == pytest.approx(0.0010)


def test_costs_make_frequent_rebalancing_lose_to_infrequent():
    """비용 모델이 실제로 물리는지 — 같은 전략을 매기간/한번만 리밸런싱해 비교."""
    rng = np.random.default_rng(7)
    panel = rng.normal(0.0, 0.02, size=(60, 3))
    model = CostModel(spread_bps=20.0)
    common = dict(weight_fn=buy_and_hold([1 / 3, 1 / 3, 1 / 3]), cost_model=model)

    often = run_backtest(_dates(60), panel, rebalance_indices=list(range(60)), **common)
    once = run_backtest(_dates(60), panel, rebalance_indices=[0], **common)

    assert often.total_cost > once.total_cost
    assert np.prod(1 + often.returns) < np.prod(1 + once.returns)


# --- 제약 연동 ---------------------------------------------------------------


def test_constraints_are_applied_to_target_weights():
    panel = np.array([[0.00, 0.00, 0.00]])
    result = run_backtest(
        _dates(1), panel, weight_fn=buy_and_hold([1.0, 0.0, 0.0]),
        rebalance_indices=[0], cost_model=FREE,
        constraints=ConstraintSet(max_weight=0.5),
    )
    assert result.weights[0].max() <= 0.5 + 1e-9
    assert result.weights[0].sum() == pytest.approx(1.0)


def test_turnover_limit_uses_current_weights_from_the_engine():
    panel = np.array([[0.00, 0.00]])
    result = run_backtest(
        _dates(1), panel, weight_fn=buy_and_hold([0.0, 1.0]),
        rebalance_indices=[0], cost_model=FREE, initial_weights=[1.0, 0.0],
        constraints=ConstraintSet(max_turnover=0.25),
    )
    assert result.turnovers[0] == pytest.approx(0.25)
    assert result.weights[0] == pytest.approx([0.75, 0.25])


# --- 비중을 정할 수 없는 시점 ------------------------------------------------


def test_weight_fn_returning_none_keeps_current_weights_and_costs_nothing():
    calls: list[int] = []

    def sometimes(t, history):
        calls.append(t)
        return None if t < 2 else [0.0, 1.0]

    panel = np.array([[0.0, 0.0]] * 3)
    result = run_backtest(
        _dates(3), panel, weight_fn=sometimes, rebalance_indices=[0, 1, 2],
        cost_model=CostModel(spread_bps=10.0), initial_weights=[1.0, 0.0],
    )
    assert calls == [0, 1, 2]
    assert result.rebalance_indices == [2]  # 실제로 집행된 것만
    assert result.costs[:2] == pytest.approx([0.0, 0.0])
    assert result.weights[0] == pytest.approx([1.0, 0.0])
    assert result.weights[2] == pytest.approx([0.0, 1.0])


def test_from_covariance_skips_until_minimum_history():
    built: list[int] = []

    def builder(history):
        built.append(len(history))
        return [0.5, 0.5]

    fn = from_covariance(builder, min_observations=5)
    panel = np.array([[0.0, 0.0]] * 8)
    result = run_backtest(
        _dates(8), panel, weight_fn=fn, rebalance_indices=[2, 4, 6],
        cost_model=FREE, initial_weights=[1.0, 0.0],
    )
    assert built == [6]  # t=2(2행), t=4(4행)는 부족 → 건너뜀
    assert result.rebalance_indices == [6]


# --- 리밸런싱 스케줄 ---------------------------------------------------------


def test_periodic_indices_pick_last_observation_of_each_period():
    dates = [date(2024, 1, 30), date(2024, 1, 31), date(2024, 2, 1), date(2024, 2, 29),
             date(2024, 3, 1)]
    assert periodic_rebalance_indices(dates, "M") == [1, 3]


def test_quarterly_and_annual_boundaries():
    dates = [date(2024, 3, 29), date(2024, 4, 1), date(2024, 12, 31), date(2025, 1, 2)]
    assert periodic_rebalance_indices(dates, "Q") == [0, 1, 2]
    assert periodic_rebalance_indices(dates, "A") == [2]


def test_periodic_indices_reject_unknown_frequency():
    with pytest.raises(ValueError, match="주기"):
        periodic_rebalance_indices(_dates(3), "W")


# --- 입력 검증 ---------------------------------------------------------------


def test_rejects_mismatched_dates_length():
    with pytest.raises(ValueError, match="dates"):
        run_backtest(_dates(2), np.array([[0.0]] * 3), weight_fn=buy_and_hold([1.0]),
                     rebalance_indices=[0], cost_model=FREE)


def test_rejects_nan_in_panel():
    panel = np.array([[0.01], [np.nan]])
    with pytest.raises(ValueError, match="NaN"):
        run_backtest(_dates(2), panel, weight_fn=buy_and_hold([1.0]),
                     rebalance_indices=[0], cost_model=FREE)


def test_rejects_out_of_range_rebalance_index():
    with pytest.raises(ValueError, match="범위"):
        run_backtest(_dates(2), np.array([[0.0]] * 2), weight_fn=buy_and_hold([1.0]),
                     rebalance_indices=[5], cost_model=FREE)


def test_rejects_initial_weights_that_do_not_sum_to_one():
    with pytest.raises(ValueError, match="합이 1"):
        run_backtest(_dates(1), np.array([[0.0, 0.0]]), weight_fn=buy_and_hold([0.5, 0.5]),
                     rebalance_indices=[0], cost_model=FREE, initial_weights=[0.5, 0.2])


def test_rejects_weight_fn_returning_wrong_length():
    with pytest.raises(ValueError, match="자산 수"):
        run_backtest(_dates(1), np.array([[0.0, 0.0]]), weight_fn=buy_and_hold([1.0]),
                     rebalance_indices=[0], cost_model=FREE)


def test_total_loss_does_not_produce_nan_weights():
    """자산이 전부 -100% 나면 정규화 분모가 0이 된다 — NaN이 이후 전 구간을
    오염시키지 않는지 확인한다."""
    panel = np.array([[-1.0, -1.0], [0.05, 0.05]])
    result = run_backtest(
        _dates(2), panel, weight_fn=buy_and_hold([0.5, 0.5]),
        rebalance_indices=[0], cost_model=FREE,
    )
    assert np.all(np.isfinite(result.returns))
    assert np.all(np.isfinite(result.weights))
    assert result.returns[0] == pytest.approx(-1.0)


def test_result_is_a_frozen_value_object():
    panel = np.array([[0.01]])
    result = run_backtest(_dates(1), panel, weight_fn=buy_and_hold([1.0]),
                          rebalance_indices=[0], cost_model=FREE)
    assert isinstance(result, BacktestResult)
    with pytest.raises(Exception):
        result.returns = np.array([0.0])  # type: ignore[misc]
