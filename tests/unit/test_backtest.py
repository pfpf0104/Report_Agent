"""워크포워드 백테스트 엔진 테스트.

핵심 관심사: 손으로 계산 가능한 케이스로 (1) 비용 없는 buy-and-hold가 실제
가격 등락을 정확히 반영하는지, (2) 거래비용이 실제로 수익률을 깎는지,
(3) 리밸런싱하지 않으면 비중이 가격 변화로 표류하는지, (4) look-ahead가
없는지(weight_fn이 미래 가격을 못 본다)를 확인한다.
"""
from datetime import date

import numpy as np
import pytest

from app.computation.portfolio.costs import CostModel
from app.computation.risk.backtest import run_backtest


def _dates(n: int) -> list[date]:
    return [date(2024, 1, 1 + i) for i in range(n)]


ZERO_COST = CostModel(spread_bps=0.0)


def test_buy_and_hold_single_asset_matches_price_return():
    """비용 0, 자산 1개, 매 시점 동일 비중(1.0) 유지 — 누적수익률은
    (마지막가격/처음가격 - 1)과 정확히 같아야 한다."""
    prices = [[100.0, 110.0, 121.0]]  # +10%, +10%

    def always_full(as_of, history):
        return np.array([1.0])

    result = run_backtest(_dates(3), prices, always_full, ZERO_COST)

    assert len(result.events) == 2
    assert result.events[0].period_return == pytest.approx(0.10)
    assert result.events[1].period_return == pytest.approx(0.10)
    assert result.cumulative_return == pytest.approx(1.21 - 1.0)


def test_two_asset_equal_weight_return_is_average_when_no_rebalance_needed():
    """두 자산이 똑같이 움직이면(상관 1.0, 동일 수익률) 동일가중 포트폴리오
    수익률도 그 값과 같아야 한다 — 가중평균의 자명한 경우."""
    prices = [
        [100.0, 110.0],  # +10%
        [50.0, 55.0],  # +10%
    ]

    def equal_weight(as_of, history):
        return np.array([0.5, 0.5])

    result = run_backtest(_dates(2), prices, equal_weight, ZERO_COST)

    assert result.events[0].period_return == pytest.approx(0.10)


def test_rebalance_cost_reduces_period_return():
    """같은 시나리오에서 비용이 0보다 크면 순수익률이 비용 없는 경우보다
    작아야 한다."""
    prices = [[100.0, 110.0]]

    def always_full(as_of, history):
        return np.array([1.0])

    costly_model = CostModel(spread_bps=50.0)  # 25bp 편도
    result_free = run_backtest(_dates(2), prices, always_full, ZERO_COST)
    result_costly = run_backtest(
        _dates(2), prices, always_full, costly_model, initial_weights=np.array([0.0])
    )

    # initial_weights=0에서 target=1.0으로 리밸런싱하므로 회전율이 발생해 비용이 붙는다.
    assert result_costly.events[0].cost > 0
    assert result_costly.events[0].period_return < result_free.events[0].period_return


def test_no_rebalance_lets_weights_drift_with_price_changes():
    """weight_fn이 None을 반환하면(판단 불가) 리밸런싱하지 않고, 다음 구간의
    시작 비중은 가격 변화로 표류한 값이어야 한다."""
    prices = [
        [100.0, 200.0, 200.0],  # 1구간: +100%
        [100.0, 100.0, 100.0],  # 1구간: 0%
    ]

    call_count = {"n": 0}

    def rebalance_once_then_hold(as_of, history):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return np.array([0.5, 0.5])
        return None  # 두 번째 구간은 리밸런싱하지 않는다

    result = run_backtest(_dates(3), prices, rebalance_once_then_hold, ZERO_COST)

    # 1구간 후 자산1 가치는 0.5*2=1.0, 자산2는 0.5*1=0.5, 합계 1.5 → 표류비중 [2/3, 1/3]
    drifted = result.events[0].drifted_weights
    assert drifted[0] == pytest.approx(2 / 3)
    assert drifted[1] == pytest.approx(1 / 3)

    # 2구간은 리밸런싱 안 했으므로 target == 표류비중이어야 하고 비용은 0이다.
    assert result.events[1].cost == pytest.approx(0.0)
    np.testing.assert_allclose(result.events[1].weights, drifted)


def test_weight_fn_only_receives_history_up_to_as_of_no_lookahead():
    """핵심 회귀: weight_fn에 넘겨지는 history 길이가 현재 시점까지만이어야
    한다 — 미래 가격이 섞이면 look-ahead bias가 생긴다."""
    prices = [[100.0, 110.0, 90.0, 130.0]]
    seen_lengths = []

    def record_history_length(as_of, history):
        seen_lengths.append(len(history[0]))
        return np.array([1.0])

    run_backtest(_dates(4), prices, record_history_length, ZERO_COST)

    # i번째 리밸런싱 시점에는 i+1개의 가격(0..i)만 보여야 한다.
    assert seen_lengths == [1, 2, 3]


def test_monthly_returns_property_matches_gips_table_input_format():
    prices = [[100.0, 110.0, 121.0]]

    def always_full(as_of, history):
        return np.array([1.0])

    result = run_backtest(_dates(3), prices, always_full, ZERO_COST)
    monthly = result.monthly_returns

    assert monthly == [(date(2024, 1, 1), pytest.approx(0.10)), (date(2024, 1, 2), pytest.approx(0.10))]


def test_rejects_mismatched_price_and_date_lengths():
    with pytest.raises(ValueError):
        run_backtest(_dates(3), [[100.0, 110.0]], lambda a, h: np.array([1.0]), ZERO_COST)


def test_rejects_single_date():
    with pytest.raises(ValueError):
        run_backtest(_dates(1), [[100.0]], lambda a, h: np.array([1.0]), ZERO_COST)


def test_rejects_weight_fn_returning_wrong_length():
    prices = [[100.0, 110.0], [50.0, 55.0]]

    def wrong_length(as_of, history):
        return np.array([1.0])  # 자산은 2개인데 1개짜리 비중 반환

    with pytest.raises(ValueError):
        run_backtest(_dates(2), prices, wrong_length, ZERO_COST)
