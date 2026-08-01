"""거래비용 모델 테스트."""
import pytest

from app.computation.portfolio.costs import (
    BP,
    CostModel,
    breakeven_alpha,
    evaluate_rebalance,
    rebalance_cost,
)


def test_one_way_cost_is_half_the_spread_without_impact():
    """스프레드 20bp면 중간가 대비 편도 10bp."""
    assert CostModel(spread_bps=20.0).one_way_cost == pytest.approx(10.0 * BP)


def test_impact_uses_square_root_of_participation():
    # 충격계수 50bp, 참여율 25% → 50 × √0.25 = 25bp
    model = CostModel(spread_bps=0.0, impact_coefficient_bps=50.0, participation_rate=0.25)
    assert model.one_way_cost == pytest.approx(25.0 * BP)


def test_impact_is_zero_at_zero_participation():
    model = CostModel(spread_bps=10.0, impact_coefficient_bps=100.0, participation_rate=0.0)
    assert model.one_way_cost == pytest.approx(5.0 * BP)


def test_square_root_impact_grows_slower_than_linear():
    """제곱근 법칙의 핵심: 참여율을 4배로 늘려도 충격은 2배만 늘어난다."""
    small = CostModel(spread_bps=0, impact_coefficient_bps=40, participation_rate=0.05).one_way_cost
    large = CostModel(spread_bps=0, impact_coefficient_bps=40, participation_rate=0.20).one_way_cost
    assert large == pytest.approx(2.0 * small)


def test_cost_model_rejects_negative_coefficients():
    with pytest.raises(ValueError):
        CostModel(spread_bps=-1.0)


def test_cost_model_rejects_invalid_participation():
    with pytest.raises(ValueError):
        CostModel(spread_bps=10.0, participation_rate=1.5)


# --- 리밸런싱 비용 ---

def test_rebalance_cost_charges_both_sides_of_the_trade():
    """회전율 10%(단방향)면 실제 거래는 매도 10% + 매수 10% = 20%.
    편도 10bp면 총 20bp × ... 즉 Σ|Δw| × 편도비용이다."""
    model = CostModel(spread_bps=20.0)  # 편도 10bp
    cost = rebalance_cost([0.6, 0.4], [0.5, 0.5], model)
    # Σ|Δw| = 0.2, 편도 10bp → 0.2 × 0.001 = 0.0002
    assert cost == pytest.approx(0.2 * 10.0 * BP)


def test_no_trade_costs_nothing():
    w = [0.3, 0.3, 0.4]
    assert rebalance_cost(w, w, CostModel(spread_bps=50.0)) == 0.0


def test_larger_turnover_costs_more():
    model = CostModel(spread_bps=20.0)
    small = rebalance_cost([0.55, 0.45], [0.5, 0.5], model)
    large = rebalance_cost([0.9, 0.1], [0.5, 0.5], model)
    assert large > small


def test_rebalance_cost_rejects_length_mismatch():
    with pytest.raises(ValueError):
        rebalance_cost([0.5, 0.5], [1.0], CostModel(spread_bps=10.0))


# --- 손익분기 판단 ---

def test_rebalance_is_worthwhile_when_alpha_exceeds_cost():
    model = CostModel(spread_bps=20.0)
    decision = evaluate_rebalance([0.6, 0.4], [0.5, 0.5], expected_gross_alpha=0.01, model=model)
    assert decision.is_worthwhile
    assert decision.net_alpha == pytest.approx(0.01 - decision.cost)


def test_rebalance_is_not_worthwhile_when_cost_eats_alpha():
    """좋은 신호도 비용을 못 넘으면 거래하지 않는 것이 낫다."""
    model = CostModel(spread_bps=200.0, impact_coefficient_bps=100.0, participation_rate=0.5)
    decision = evaluate_rebalance([0.9, 0.1], [0.1, 0.9], expected_gross_alpha=0.001, model=model)
    assert not decision.is_worthwhile
    assert decision.net_alpha < 0


def test_decision_reports_turnover_using_one_way_convention():
    decision = evaluate_rebalance([0.6, 0.4], [0.5, 0.5], 0.01, CostModel(spread_bps=10.0))
    assert decision.turnover == pytest.approx(0.10)


def test_breakeven_alpha_equals_the_cost():
    model = CostModel(spread_bps=30.0)
    new, cur = [0.7, 0.3], [0.5, 0.5]
    assert breakeven_alpha(new, cur, model) == pytest.approx(rebalance_cost(new, cur, model))


def test_decision_exactly_at_breakeven_is_not_worthwhile():
    """정확히 손익분기면 실행 이유가 없다 — 부등호가 > 인지 >= 인지 고정한다."""
    model = CostModel(spread_bps=30.0)
    new, cur = [0.7, 0.3], [0.5, 0.5]
    decision = evaluate_rebalance(new, cur, breakeven_alpha(new, cur, model), model)
    assert decision.net_alpha == pytest.approx(0.0)
    assert not decision.is_worthwhile


def test_describe_includes_assumptions_for_report_disclosure():
    """리포트에 비용 가정을 표시해야 하므로 문자열에 계수가 드러나야 한다."""
    text = CostModel(spread_bps=20.0, impact_coefficient_bps=50.0, participation_rate=0.25).describe()
    assert "20.0bp" in text and "50.0bp" in text and "25.0%" in text
