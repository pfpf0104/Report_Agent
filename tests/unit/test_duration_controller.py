from datetime import date

import pytest

from app.computation.fixed_income.duration_controller import (
    D_LONG_YEARS,
    D_SHORT_YEARS,
    HORIZON_TRADING_DAYS,
    CarryPriceGate,
    DurationLot,
    LotLedger,
    _index_weight_split,
    _q_hat_sensitivity_rows,
    _trailing_month_ends,
    build_metroguard_context,
    compute_carry_price_gate,
    compute_target_duration,
    compute_warning,
)
from app.db.base import SessionLocal


def test_compute_carry_price_gate_formula_matches_manual_calculation():
    gate = compute_carry_price_gate(predicted_change_bp=50.0, yield_3y_bp=100.0, yield_1y_bp=50.0)
    spread_bp = 100.0 - 50.0
    expected_a_minus = (D_LONG_YEARS - D_SHORT_YEARS) * 50.0 - spread_bp * (HORIZON_TRADING_DAYS / 252)
    assert gate.curve_spread_bp == pytest.approx(spread_bp)
    assert gate.a_minus_bp == pytest.approx(expected_a_minus)


@pytest.mark.parametrize("predicted_change_bp,a_minus_bp", [(-5.0, 27.5), (0.0, 27.5), (5.0, -1.0)])
def test_compute_warning_is_zero_unless_both_q_hat_and_a_minus_positive(predicted_change_bp, a_minus_bp):
    gate = CarryPriceGate(predicted_change_bp, 0.0, a_minus_bp)
    assert compute_warning(gate) == 0.0


def test_compute_warning_matches_reference_report_worked_example():
    """첨부 MetroGuard-KR 보고서의 지면 예시: A⁻=27.5bp -> g≈0.968 (모듈 docstring 참고)."""
    gate = CarryPriceGate(predicted_change_bp=5.0, curve_spread_bp=0.0, a_minus_bp=27.5)
    assert compute_warning(gate) == pytest.approx(0.968, abs=0.001)


def test_compute_target_duration_single_lot_matches_reference_report_worked_example():
    """지면 예시: 단일 lot g≈0.968 -> D*≈1.06년."""
    ledger = LotLedger()
    ledger.add_lot(date(2026, 6, 30), 0.9679132552046507)
    d_star = compute_target_duration(ledger, date(2026, 7, 15))
    assert d_star == pytest.approx(1.06, abs=0.01)


def test_compute_target_duration_none_when_no_active_lots():
    ledger = LotLedger()
    assert compute_target_duration(ledger, date(2026, 7, 30)) is None


def test_active_lots_excludes_lot_exactly_at_horizon_boundary():
    """생성 후 정확히 HORIZON_TRADING_DAYS 지난 lot은 활성에서 빠져야 한다(< 비교, <= 아님)."""
    ledger = LotLedger()
    origin = date(2026, 1, 2)
    ledger.add_lot(origin, 0.5)

    # origin으로부터 정확히 HORIZON_TRADING_DAYS 평일 뒤인 날짜를 찾는다.
    import numpy as np

    as_of = origin
    while np.busday_count(origin, as_of) < HORIZON_TRADING_DAYS:
        as_of = date.fromordinal(as_of.toordinal() + 1)
    assert np.busday_count(origin, as_of) == HORIZON_TRADING_DAYS

    assert ledger.active_lots(as_of) == []
    day_before = date.fromordinal(as_of.toordinal() - 1)
    assert len(ledger.active_lots(day_before)) == 1


def test_trailing_month_ends_includes_current_month_when_as_of_is_month_end():
    origins = _trailing_month_ends(date(2026, 7, 31), count=4)
    assert origins == [date(2026, 4, 30), date(2026, 5, 31), date(2026, 6, 30), date(2026, 7, 31)]


def test_trailing_month_ends_excludes_current_month_end_not_yet_reached():
    """as_of가 월말 전이면(예: 7/30) 이번 달 origin은 아직 발생하지 않은 것으로 보고
    count보다 적은 개수만 반환한다 — build_metroguard_context가 이 세션 내내 실제로
    쓴 date(2026, 7, 30)이 정확히 이 경로를 타는 것을 확인한다."""
    origins = _trailing_month_ends(date(2026, 7, 30), count=4)
    assert origins == [date(2026, 4, 30), date(2026, 5, 31), date(2026, 6, 30)]
    assert len(origins) == 3


def test_trailing_month_ends_handles_year_boundary():
    origins = _trailing_month_ends(date(2026, 2, 1), count=3)
    assert origins == [date(2025, 12, 31), date(2026, 1, 31)]


@pytest.mark.parametrize(
    "d_star,expected_short_pct,expected_long_pct",
    [(D_SHORT_YEARS, 100.0, 0.0), (D_LONG_YEARS, 0.0, 100.0), (2.0, 50.0, 50.0)],
)
def test_index_weight_split_anchors_span_full_d_star_range(d_star, expected_short_pct, expected_long_pct):
    """앵커가 실제 D* 범위(D_SHORT_YEARS~D_LONG_YEARS)와 어긋나면 100/0으로 굳어버리는
    버그가 있었다(2.5~4.0년 앵커였을 때 실제 재현됨) — 전체 범위에서 선형으로 움직이는지
    회귀 확인한다."""
    weights = _index_weight_split(d_star)
    assert weights["1-3년 국채지수"] == pytest.approx(expected_short_pct)
    assert weights["3-5년 국채지수"] == pytest.approx(expected_long_pct)


def test_q_hat_sensitivity_non_positive_q_hat_always_gives_zero_warning():
    """AUTHORITY 설계(AI 자본권한은 단축으로만 제한)의 회귀 확인: q̂<=0이면 g=0."""
    rows = _q_hat_sensitivity_rows([], curve_spread_bp=0.0, deltas_bp=(-20.0, -10.0, 0.0))
    for row in rows:
        assert row[1] == "0.000"


def test_build_metroguard_context_smoke():
    db = SessionLocal()
    try:
        context = build_metroguard_context(db, date(2026, 7, 30))
    finally:
        db.close()

    for key in (
        "headline",
        "cards",
        "ledger_rows",
        "formula_cards",
        "workflow_steps",
        "checklist_items",
        "performance_available",
        "sensitivity_rows",
        "warning_function_chart_uri",
        "historical_g_chart_uri",
        "glossary_cards",
        "source",
        "cross_asset_available",
        "regime_available",
        "disclosure_available",
        "lineage_rows",
    ):
        assert key in context, f"{key} 누락"

    # 성과 이력 충족 여부는 DB 상태에 따라 달라진다 — 두 경로 모두 컨텍스트가
    # 완결돼 있는지만 확인한다(어느 쪽이든 렌더 가능해야 한다).
    if context["performance_available"]:
        assert context["gips_rows"]
        assert context["risk_metric_rows"]
    else:
        assert "gips_requirements" in context

    for uri_key in ("warning_function_chart_uri", "historical_g_chart_uri"):
        assert context[uri_key].startswith("data:image/png;base64,")
