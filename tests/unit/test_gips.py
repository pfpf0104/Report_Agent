"""GIPS 컴포지트 표 테스트.

핵심 관심사는 "표가 채워지는가"가 아니라 **채우면 안 되는 칸을 비워 두는가**다.
36개월이 안 되는 연도에 12개월치로 계산한 값을 "3년 표준편차"라 표기하면 표는
완성돼 보이지만 라벨이 거짓이 된다.
"""
from datetime import date

import pytest

from app.computation.risk.gips import (
    build_gips_table,
    format_gips_table_rows,
    meets_gips_minimum_history,
)


def _monthly(start_year: int, n_months: int, monthly_return: float):
    """start_year 1월부터 n_months개월치 (월말일자, 수익률)."""
    out = []
    y, m = start_year, 1
    for _ in range(n_months):
        # 월말 근사(28일) — 연도 그룹핑만 쓰므로 일자 정확도는 무관하다.
        out.append((date(y, m, 28), monthly_return))
        m += 1
        if m > 12:
            m = 1
            y += 1
    return out


def test_annual_return_compounds_monthly_returns():
    # 월 1% × 12개월 → 1.01^12 − 1 = 12.6825%
    rows = build_gips_table(_monthly(2021, 12, 0.01))
    assert len(rows) == 1
    assert rows[0].portfolio_return == pytest.approx(0.126825, abs=1e-6)


def test_partial_year_is_not_annualized():
    """3개월만 있는 해를 연환산하면 실제로 벌지 않은 수익을 표기하게 된다."""
    rows = build_gips_table(_monthly(2021, 3, 0.01))
    # 1.01^3 − 1 = 3.0301% 이지 12.68%가 아니다
    assert rows[0].portfolio_return == pytest.approx(0.030301, abs=1e-6)
    assert rows[0].is_partial_year


def test_3yr_stdev_is_none_before_36_months_accumulate():
    rows = build_gips_table(_monthly(2021, 24, 0.01))  # 2년치
    for row in rows:
        assert row.portfolio_3yr_stdev is None, f"{row.year}년에 36개월 미만인데 값이 채워졌다"


def test_3yr_stdev_appears_once_36_months_available():
    rows = build_gips_table(_monthly(2021, 36, 0.01))
    by_year = {r.year: r for r in rows}
    assert by_year[2021].portfolio_3yr_stdev is None
    assert by_year[2022].portfolio_3yr_stdev is None
    assert by_year[2023].portfolio_3yr_stdev is not None  # 36개월 도달


def test_benchmark_columns_are_none_without_benchmark():
    rows = build_gips_table(_monthly(2021, 12, 0.01))
    assert rows[0].benchmark_return is None
    assert rows[0].excess_return is None


def test_excess_return_is_portfolio_minus_benchmark():
    p = _monthly(2021, 12, 0.01)
    b = _monthly(2021, 12, 0.005)
    rows = build_gips_table(p, b)
    row = rows[0]
    assert row.excess_return == pytest.approx(row.portfolio_return - row.benchmark_return)
    assert row.excess_return > 0


def test_minimum_history_requires_five_full_years():
    assert not meets_gips_minimum_history(build_gips_table(_monthly(2021, 48, 0.01)))  # 4년
    assert meets_gips_minimum_history(build_gips_table(_monthly(2021, 60, 0.01)))      # 5년


def test_minimum_history_does_not_count_partial_years():
    """5년 + 1개월이면 완전한 해는 5개 — 부분연도를 세면 6년으로 착각한다."""
    rows = build_gips_table(_monthly(2021, 61, 0.01))
    full = [r for r in rows if not r.is_partial_year]
    assert len(full) == 5
    assert meets_gips_minimum_history(rows)


def test_formatted_rows_use_dash_not_zero_for_missing_values():
    """계산 불가한 칸을 0.00%로 채우면 '변동성이 0'이라는 거짓 정보가 된다."""
    rows = build_gips_table(_monthly(2021, 12, 0.01))
    formatted = format_gips_table_rows(rows)
    assert formatted[0][2] == "—"  # 벤치마크 수익률 없음
    assert formatted[0][4] == "—"  # 3년 표준편차 미충족


def test_formatted_partial_year_is_marked():
    rows = build_gips_table(_monthly(2021, 3, 0.01))
    assert format_gips_table_rows(rows)[0][0] == "2021*"


def test_empty_input_produces_empty_table():
    assert build_gips_table([]) == []
    assert not meets_gips_minimum_history([])
