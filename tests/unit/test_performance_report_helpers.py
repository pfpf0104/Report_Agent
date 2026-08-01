"""성과 컨텍스트의 순수 함수 — DB 없이 손으로 검산한다."""
from datetime import date

import numpy as np
import pytest

from app.computation.portfolio.constraints import ConstraintSet, relax_cap_to_feasible
from app.computation.risk.report_context import build_risk_metric_rows, to_monthly


# --- 일간 → 월간 -------------------------------------------------------------


def test_monthly_return_is_compounded_not_summed():
    dates = [date(2024, 1, 10), date(2024, 1, 20), date(2024, 1, 31)]
    monthly = to_monthly(dates, np.array([0.10, 0.10, 0.10]))

    assert len(monthly) == 1
    assert monthly[0][0] == date(2024, 1, 31)
    assert monthly[0][1] == pytest.approx(1.1**3 - 1)  # 0.331, 산술합 0.30이 아니다


def test_monthly_buckets_split_on_month_boundaries():
    dates = [date(2024, 1, 30), date(2024, 1, 31), date(2024, 2, 1), date(2024, 2, 29)]
    monthly = to_monthly(dates, np.array([0.01, 0.02, 0.03, 0.04]))

    assert [d for d, _ in monthly] == [date(2024, 1, 31), date(2024, 2, 29)]
    assert monthly[0][1] == pytest.approx(1.01 * 1.02 - 1)
    assert monthly[1][1] == pytest.approx(1.03 * 1.04 - 1)


def test_monthly_label_is_the_last_observed_day_not_the_calendar_month_end():
    """마지막 거래일이 달력 말일이 아닐 수 있다(휴장). 관측된 날짜를 쓴다."""
    dates = [date(2024, 3, 28), date(2024, 3, 29)]  # 3/31은 일요일
    monthly = to_monthly(dates, np.array([0.01, 0.01]))
    assert monthly[0][0] == date(2024, 3, 29)


def test_monthly_spans_year_boundary():
    dates = [date(2023, 12, 29), date(2024, 1, 2)]
    monthly = to_monthly(dates, np.array([0.01, 0.02]))
    assert [d.year for d, _ in monthly] == [2023, 2024]


def test_monthly_of_empty_input_is_empty():
    assert to_monthly([], np.array([])) == []


# --- 지표 표 -----------------------------------------------------------------


def _rows_as_dict(rows):
    return {r[0]: (r[1], r[2]) for r in rows}


def test_metric_table_puts_benchmark_in_its_own_column():
    rng = np.random.default_rng(3)
    bench = rng.normal(0.0004, 0.01, 500)
    port = bench * 1.2

    table = _rows_as_dict(build_risk_metric_rows(port, bench))

    assert table["베타"][0] == "1.20"
    assert table["베타"][1] == "1.00"
    assert table["연환산 변동성"][0] != table["연환산 변동성"][1]


def test_var_is_shown_as_a_loss_magnitude_without_a_plus_sign():
    """historical_var는 손실을 양수로 준다 — '+3.10%'로 찍히면 이익처럼 읽힌다."""
    returns = np.array([-0.05] * 10 + [0.01] * 90)
    table = _rows_as_dict(build_risk_metric_rows(returns, None))

    var_cell = table["VaR 95% (일간 손실)"][0]
    assert not var_cell.startswith("+")
    assert var_cell.endswith("%")
    assert float(var_cell.rstrip("%")) > 0


def test_undefined_metrics_render_as_dash_not_zero():
    """상수 수익률에서는 Sharpe·Calmar가 정의되지 않는다."""
    table = _rows_as_dict(build_risk_metric_rows(np.full(300, 0.001), None))

    assert table["Sharpe"][0] == "—"
    assert table["Calmar"][0] == "—"  # 낙폭이 없어 정의되지 않는다
    assert table["최대낙폭"][0] == "+0.00%"


def test_benchmark_column_is_dash_when_no_benchmark_given():
    rows = build_risk_metric_rows(np.random.default_rng(1).normal(0, 0.01, 300), None)
    assert all(r[2] == "—" for r in rows)
    # 벤치마크 전용 지표(베타·추적오차·정보비율)는 아예 행이 생기지 않는다
    assert "베타" not in {r[0] for r in rows}


def test_value_rounding_to_zero_does_not_keep_a_minus_sign():
    """베타 -0.0001이 '-0.00'으로 찍히면 독자가 실제 음수로 읽는다."""
    bench = np.array([0.01, -0.01] * 150)
    port = np.array([1e-6, 1e-6] * 150)  # 벤치마크와 사실상 무관
    table = _rows_as_dict(build_risk_metric_rows(port, bench))
    assert not table["베타"][0].startswith("-0.00")


def test_disclosure_strings_contain_no_markdown_emphasis():
    """공시 문구는 Jinja가 그대로 출력한다 — '**'를 쓰면 지면에 별표가 찍힌다."""
    from app.computation.risk.report_context import (
        HYPOTHETICAL_DISCLOSURE,
        NEUTRAL_STRATEGY_DISCLOSURE,
    )

    for text in (HYPOTHETICAL_DISCLOSURE, NEUTRAL_STRATEGY_DISCLOSURE):
        assert "**" not in text
        assert "__" not in text


def test_unrecovered_drawdown_is_labelled_not_left_blank():
    returns = np.array([0.05] * 10 + [-0.02] * 40)  # 끝까지 회복 못 함
    table = _rows_as_dict(build_risk_metric_rows(returns, None))
    assert table["낙폭 회복"][0] == "미회복"


# --- 상한 완화 ---------------------------------------------------------------


def test_cap_below_one_over_n_is_relaxed_to_exactly_one_over_n():
    relaxed, changed = relax_cap_to_feasible(ConstraintSet(max_weight=0.25), n_assets=3)
    assert changed is True
    assert relaxed.max_weight == pytest.approx(1 / 3)


def test_feasible_cap_is_left_untouched():
    original = ConstraintSet(max_weight=0.25)
    relaxed, changed = relax_cap_to_feasible(original, n_assets=11)
    assert changed is False
    assert relaxed is original


def test_cap_exactly_at_one_over_n_is_not_reported_as_relaxed():
    relaxed, changed = relax_cap_to_feasible(ConstraintSet(max_weight=0.2), n_assets=5)
    assert changed is False
    assert relaxed.max_weight == pytest.approx(0.2)


def test_no_cap_means_nothing_to_relax():
    original = ConstraintSet(max_turnover=0.1)
    relaxed, changed = relax_cap_to_feasible(original, n_assets=3)
    assert changed is False
    assert relaxed is original


def test_relaxation_preserves_other_constraints():
    relaxed, _ = relax_cap_to_feasible(
        ConstraintSet(max_weight=0.1, min_weight=0.02, max_turnover=0.3), n_assets=4
    )
    assert relaxed.max_weight == pytest.approx(0.25)
    assert relaxed.min_weight == pytest.approx(0.02)
    assert relaxed.max_turnover == pytest.approx(0.3)
