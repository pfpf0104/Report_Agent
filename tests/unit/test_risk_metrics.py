"""성과·리스크 지표 테스트.

기관 리포트에 실릴 숫자이므로 "돌아간다"가 아니라 **손으로 검산 가능한 값과
일치하는가**를 확인한다. 대부분의 케이스는 기대값을 주석에 계산 과정과 함께
남겼다.
"""
import numpy as np
import pytest

from app.computation.risk.metrics import (
    alpha,
    annualized_return,
    annualized_volatility,
    beta,
    calmar_ratio,
    conditional_var,
    cumulative_return,
    downside_deviation,
    historical_var,
    information_ratio,
    max_drawdown,
    returns_from_prices,
    sharpe_ratio,
    sortino_ratio,
    tracking_error,
)


# --- 기본 변환 ---

def test_returns_from_prices():
    # 100 → 110 → 99 : +10%, -10%
    assert returns_from_prices([100, 110, 99]) == pytest.approx([0.10, -0.10])


def test_returns_from_prices_too_short_returns_empty():
    assert len(returns_from_prices([100])) == 0


def test_returns_from_prices_rejects_zero_price():
    with pytest.raises(ValueError):
        returns_from_prices([0, 100])


def test_cumulative_return_compounds_not_sums():
    # +10% 후 -10% 는 0%가 아니라 -1% (1.1 × 0.9 = 0.99)
    assert cumulative_return([0.10, -0.10]) == pytest.approx(-0.01)


# --- 연환산 ---

def test_annualized_return_is_geometric():
    # 월간 1%가 12개월 → 1.01^12 − 1 = 12.6825%
    assert annualized_return([0.01] * 12, periods_per_year=12) == pytest.approx(0.126825, abs=1e-6)


def test_annualized_return_over_multiple_years():
    # 2년간 월 0.5% → (1.005^24)^(12/24) − 1 = 1.005^12 − 1 = 6.1678%
    assert annualized_return([0.005] * 24, periods_per_year=12) == pytest.approx(0.061678, abs=1e-6)


def test_annualized_return_total_loss_floors_at_minus_100pct():
    """누적 -100%면 기하평균이 정의되지 않는다 — NaN/예외 대신 -1.0으로 고정."""
    assert annualized_return([-1.0, 0.5], periods_per_year=12) == -1.0


def test_annualized_volatility_uses_sample_stdev():
    r = [0.01, -0.01, 0.02, -0.02]
    expected = np.std(r, ddof=1) * np.sqrt(12)
    assert annualized_volatility(r, periods_per_year=12) == pytest.approx(expected)


def test_constant_returns_have_zero_volatility():
    assert annualized_volatility([0.01] * 10, periods_per_year=12) == 0.0


# --- Sharpe / Sortino ---

def test_sharpe_ratio_matches_manual_calculation():
    r = [0.02, -0.01, 0.03, 0.00, 0.01]
    expected = (annualized_return(r, 12) - 0.03) / annualized_volatility(r, 12)
    assert sharpe_ratio(r, risk_free_rate=0.03, periods_per_year=12) == pytest.approx(expected)


def test_sharpe_is_none_when_volatility_is_zero():
    """무의미한 inf를 리포트에 싣지 않기 위해 None."""
    assert sharpe_ratio([0.01] * 10, periods_per_year=12) is None


def test_downside_deviation_ignores_upside():
    """상방 변동은 위험이 아니다 — 하방만 반영되는지 확인."""
    only_up = [0.05, 0.05, 0.05]
    assert downside_deviation(only_up, target=0.0, periods_per_year=12) == 0.0


def test_downside_deviation_divides_by_full_sample_size():
    # 4개 중 1개만 -2%: sqrt(0.02^2 / 4) × sqrt(12)
    r = [0.01, 0.01, 0.01, -0.02]
    expected = np.sqrt((0.02**2) / 4) * np.sqrt(12)
    assert downside_deviation(r, target=0.0, periods_per_year=12) == pytest.approx(expected)


def test_sortino_exceeds_sharpe_when_downside_is_rare():
    """상승이 잦고 하락이 드문 시계열에서는 Sortino가 Sharpe보다 커야 한다."""
    r = [0.03, 0.03, 0.03, -0.01, 0.03, 0.03]
    assert sortino_ratio(r, periods_per_year=12) > sharpe_ratio(r, periods_per_year=12)


def test_sortino_is_none_without_any_downside():
    assert sortino_ratio([0.01] * 5, periods_per_year=12) is None


# --- 최대낙폭 ---

def test_max_drawdown_simple_path():
    # 부의 경로: 1.0 → 1.2 → 0.9 → 1.0
    # 고점 1.2 대비 저점 0.9 → -25%
    r = returns_from_prices([1.0, 1.2, 0.9, 1.0])
    result = max_drawdown(r)
    assert result.max_drawdown == pytest.approx(-0.25)


def test_max_drawdown_records_recovery_when_peak_regained():
    # 100 → 120 → 90 → 130 : 고점 120을 130에서 회복
    r = returns_from_prices([100, 120, 90, 130])
    result = max_drawdown(r)
    assert result.recovery_index is not None
    assert result.trough_index < result.recovery_index


def test_max_drawdown_recovery_is_none_when_never_regained():
    """미회복 낙폭과 회복된 낙폭을 구분하지 못하면 리스크를 오독한다."""
    r = returns_from_prices([100, 120, 90, 110])  # 120을 회복 못 함
    assert max_drawdown(r).recovery_index is None


def test_max_drawdown_is_zero_for_monotonic_rise():
    result = max_drawdown([0.01, 0.02, 0.01])
    assert result.max_drawdown == 0.0
    assert result.recovery_index is None


def test_max_drawdown_empty_series():
    assert max_drawdown([]).max_drawdown == 0.0


def test_calmar_is_none_without_drawdown():
    assert calmar_ratio([0.01, 0.01], periods_per_year=12) is None


def test_calmar_matches_manual_calculation():
    r = returns_from_prices([100, 120, 90, 130])
    expected = annualized_return(r, 12) / abs(max_drawdown(r).max_drawdown)
    assert calmar_ratio(r, periods_per_year=12) == pytest.approx(expected)


# --- VaR / CVaR ---

def test_historical_var_returns_positive_loss():
    """VaR은 손실을 양수로 반환한다 — 부호 혼동은 리스크 리포트의 전형적 오류."""
    r = [-0.05, -0.02, 0.0, 0.01, 0.03]
    assert historical_var(r, confidence=0.80) > 0


def test_historical_var_at_known_percentile():
    # 100개 균등 분포 -0.05 ~ 0.05, 95% VaR = 5번째 백분위수의 부호 반전
    r = np.linspace(-0.05, 0.05, 101)
    expected = -np.percentile(r, 5)
    assert historical_var(r, confidence=0.95) == pytest.approx(expected)


def test_cvar_is_worse_than_var():
    """CVaR은 VaR을 넘는 손실의 평균이므로 항상 VaR 이상이어야 한다."""
    rng = np.random.default_rng(42)
    r = rng.normal(0.0005, 0.02, size=1000)
    assert conditional_var(r, 0.95) >= historical_var(r, 0.95)


def test_var_rejects_invalid_confidence():
    with pytest.raises(ValueError):
        historical_var([0.01, -0.01], confidence=1.5)


# --- 벤치마크 상대지표 ---

def test_tracking_error_is_zero_when_identical():
    r = [0.01, -0.02, 0.03]
    assert tracking_error(r, r, periods_per_year=12) == 0.0


def test_information_ratio_is_none_when_tracking_error_zero():
    r = [0.01, -0.02, 0.03]
    assert information_ratio(r, r, periods_per_year=12) is None


def test_beta_of_identical_series_is_one():
    r = [0.01, -0.02, 0.03, 0.005]
    assert beta(r, r) == pytest.approx(1.0)


def test_beta_of_double_leverage_is_two():
    b = [0.01, -0.02, 0.03, 0.005]
    p = [2 * x for x in b]
    assert beta(p, b) == pytest.approx(2.0)


def test_beta_is_none_when_benchmark_has_no_variance():
    assert beta([0.01, 0.02], [0.01, 0.01]) is None


def test_alpha_is_zero_when_portfolio_tracks_benchmark_exactly():
    r = [0.01, -0.02, 0.03, 0.005]
    assert alpha(r, r, risk_free_rate=0.0, periods_per_year=12) == pytest.approx(0.0, abs=1e-12)


def test_mismatched_lengths_raise():
    with pytest.raises(ValueError):
        tracking_error([0.01, 0.02], [0.01])
