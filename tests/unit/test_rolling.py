"""롤링 분석 검증.

핵심 관심사는 두 가지다.
  1) 정렬 — 값이 실제로 어느 창에서 나왔는지가 end_indices와 정확히 맞는가.
  2) 정의되지 않는 구간을 0으로 뭉개지 않는가(이 프로젝트가 GIPS 표준편차에서
     겪을 뻔한 문제와 같은 종류다).
"""
from datetime import date, timedelta

import numpy as np
import pytest

from app.computation.risk.rolling import (
    RollingSeries,
    rolling_apply,
    rolling_beta,
    rolling_correlation,
    rolling_max_drawdown,
    rolling_return,
    rolling_sharpe,
    rolling_tracking_error,
    rolling_volatility,
    summarize,
)


def _dates(n: int) -> list[date]:
    return [date(2024, 1, 1) + timedelta(days=30 * i) for i in range(n)]


# --- 정렬 -------------------------------------------------------------------


def test_window_covers_exactly_the_last_n_observations():
    seen: list[list[float]] = []

    def spy(w):
        seen.append(list(w))
        return float(w.sum())

    series = rolling_apply([1, 2, 3, 4, 5], window=3, fn=spy)

    assert seen == [[1, 2, 3], [2, 3, 4], [3, 4, 5]]
    assert series.end_indices == [2, 3, 4]  # 창의 마지막 관측치 인덱스
    assert series.values == pytest.approx([6.0, 9.0, 12.0])


def test_result_length_is_n_minus_window_plus_one():
    series = rolling_apply(np.zeros(10), window=4, fn=lambda w: 0.0)
    assert len(series.values) == 10 - 4 + 1


def test_insufficient_history_returns_empty_not_error():
    """백필 중에는 이력이 창보다 짧은 것이 정상이다 — 예외가 아니라 빈 결과."""
    series = rolling_volatility([0.01, 0.02], window=12)
    assert series.is_empty
    assert series.values == []
    assert series.end_indices == []


def test_window_equal_to_length_gives_exactly_one_point():
    series = rolling_return([0.1, 0.1, 0.1], window=3)
    assert len(series.values) == 1
    assert series.values[0] == pytest.approx(1.1**3 - 1)


def test_rejects_window_below_two():
    with pytest.raises(ValueError, match="창 크기"):
        rolling_apply([0.1, 0.2, 0.3], window=1, fn=lambda w: 0.0)


def test_labels_use_the_last_date_of_each_window():
    series = rolling_apply(np.zeros(5), window=3, fn=lambda w: 0.0)
    labels = series.labels(_dates(5), fmt="%Y-%m-%d")
    assert labels == ["2024-03-01", "2024-03-31", "2024-04-30"]


# --- 값 검산 ----------------------------------------------------------------


def test_rolling_return_is_geometric_not_arithmetic():
    series = rolling_return([0.10, 0.10, -0.10], window=2)
    # (1.1×1.1)-1 = 0.21 (산술합 0.20이 아니다), (1.1×0.9)-1 = -0.01
    assert series.values == pytest.approx([0.21, -0.01])


def test_rolling_volatility_matches_hand_computed_sample_stdev():
    returns = [0.01, -0.01, 0.01, -0.01]
    series = rolling_volatility(returns, window=3, periods_per_year=12)
    expected = [
        float(np.std(returns[0:3], ddof=1) * np.sqrt(12)),
        float(np.std(returns[1:4], ddof=1) * np.sqrt(12)),
    ]
    assert series.values == pytest.approx(expected)


def test_rolling_max_drawdown_only_looks_inside_the_window():
    """창 밖의 고점은 낙폭 계산에 쓰이지 않는다 — 전구간 MDD보다 얕아야 한다."""
    returns = [0.50, -0.10, -0.10, -0.10]
    series = rolling_max_drawdown(returns, window=2)
    # 각 2기 창: [0.5,-0.1] → -10%, [-0.1,-0.1] → -19%, [-0.1,-0.1] → -19%
    assert series.values == pytest.approx([-0.10, -0.19, -0.19])


def test_rolling_correlation_detects_regime_change():
    """전구간 상관계수 하나로는 안 보이는 국면 전환을 롤링은 잡아낸다."""
    a = [0.01, 0.02, 0.03, 0.04, -0.01, -0.02, -0.03, -0.04]
    b = [0.01, 0.02, 0.03, 0.04, 0.01, 0.02, 0.03, 0.04]  # 후반부에 반대로 움직임

    series = rolling_correlation(a, b, window=4)
    assert series.values[0] == pytest.approx(1.0)  # 전반부: 완전 동조
    assert series.values[-1] == pytest.approx(-1.0)  # 후반부: 완전 역행

    overall = float(np.corrcoef(a, b)[0, 1])
    assert abs(overall) < 0.9  # 전구간 값은 두 국면을 뭉개 버린다


def test_rolling_beta_recovers_the_true_slope():
    benchmark = [0.01, -0.02, 0.03, -0.01, 0.02, 0.01]
    portfolio = [1.5 * r for r in benchmark]
    series = rolling_beta(portfolio, benchmark, window=4)
    assert all(v == pytest.approx(1.5) for v in series.values)


def test_rolling_tracking_error_is_zero_when_portfolio_equals_benchmark():
    r = [0.01, -0.02, 0.03, -0.01, 0.02]
    series = rolling_tracking_error(r, r, window=3, periods_per_year=12)
    assert series.values == pytest.approx([0.0, 0.0, 0.0])


def test_pair_functions_reject_length_mismatch():
    with pytest.raises(ValueError, match="길이가 다르다"):
        rolling_beta([0.01, 0.02, 0.03], [0.01, 0.02], window=2)


# --- 정의되지 않는 구간 ------------------------------------------------------


def test_sharpe_is_none_not_zero_when_window_has_no_volatility():
    """상수 수익률 창에서 Sharpe는 정의되지 않는다. 0으로 찍히면 안 된다."""
    returns = [0.01, 0.01, 0.01, 0.05, 0.02]
    series = rolling_sharpe(returns, window=3, periods_per_year=12)
    assert series.values[0] is None
    assert all(v is not None for v in series.values[1:])


def test_correlation_is_none_when_one_side_is_constant():
    series = rolling_correlation([0.01, 0.02, 0.03], [0.01, 0.01, 0.01], window=3)
    assert series.values == [None]


def test_beta_is_none_when_benchmark_is_constant():
    series = rolling_beta([0.01, 0.02, 0.03], [0.01, 0.01, 0.01], window=3)
    assert series.values == [None]


def test_defined_values_excludes_none():
    series = RollingSeries(window=3, end_indices=[2, 3, 4], values=[None, 1.0, 2.0])
    assert series.defined_values == [1.0, 2.0]


def test_to_plot_values_fills_none_explicitly():
    series = RollingSeries(window=3, end_indices=[2, 3], values=[None, 1.5])
    assert series.to_plot_values() == [0.0, 1.5]
    assert series.to_plot_values(fill=float("nan"))[1] == 1.5


def test_series_rejects_mismatched_lengths():
    with pytest.raises(ValueError, match="길이가 다르다"):
        RollingSeries(window=3, end_indices=[2, 3], values=[1.0])


# --- 요약 -------------------------------------------------------------------


def test_summarize_ignores_undefined_points_in_min_max_average():
    series = RollingSeries(window=12, end_indices=[11, 12, 13], values=[None, 1.0, 3.0])
    s = summarize(series, "Sharpe")
    assert s.minimum == pytest.approx(1.0)
    assert s.maximum == pytest.approx(3.0)
    assert s.average == pytest.approx(2.0)
    assert s.latest == pytest.approx(3.0)
    assert s.undefined_count == 1
    assert "정의 불가 1개" in s.describe()


def test_summarize_of_empty_series_reports_insufficient_history():
    s = summarize(RollingSeries(window=12, end_indices=[], values=[]), "Sharpe")
    assert s.observations == 0
    assert s.average is None
    assert "이력 부족" in s.describe()


def test_summarize_latest_is_none_when_last_window_is_undefined():
    series = RollingSeries(window=3, end_indices=[2, 3], values=[1.0, None])
    s = summarize(series, "Sharpe")
    assert s.latest is None
    assert "최근 —" in s.describe()
