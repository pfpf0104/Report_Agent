"""scenario_rationale.py — Phase 4-5 시나리오 확률가중치 근거 문서화.

DB가 필요 없다 — 순수 계산 함수와 정적 콘텐츠만 다룬다.
"""
import pytest

from app.computation.valuation.residual_income_model import SAMSUNG_BOOK_VALUE, SAMSUNG_SCENARIOS, SK_HYNIX_BOOK_VALUE, SK_HYNIX_SCENARIOS
from app.computation.valuation.scenario_rationale import (
    SCENARIO_PROBABILITY_RATIONALE,
    build_scenario_rationale_context,
    probability_weight_sensitivity,
)


def test_scenario_probability_rationale_weights_sum_to_one():
    total = sum(row["weight"] for row in SCENARIO_PROBABILITY_RATIONALE)
    assert total == pytest.approx(1.0)


def test_scenario_probability_rationale_matches_actual_scenario_weights():
    """근거 문서의 가중치가 실제 RimScenario 가중치와 어긋나면 안 된다 —
    설명과 계산이 따로 노는 회귀를 잡는다."""
    rationale_by_name = {row["scenario"]: row["weight"] for row in SCENARIO_PROBABILITY_RATIONALE}
    for sc in SAMSUNG_SCENARIOS:
        assert rationale_by_name[sc.name] == pytest.approx(sc.weight)


def test_probability_weight_sensitivity_zero_shift_matches_base_value():
    rows = probability_weight_sensitivity(SAMSUNG_BOOK_VALUE, SAMSUNG_SCENARIOS, shift_pct_pt=10.0)
    zero_shift = next(r for r in rows if r["base_case_weight_pct"] == pytest.approx(50.0))
    assert zero_shift["change_pct"] == pytest.approx(0.0, abs=1e-6)


def test_probability_weight_sensitivity_preserves_total_weight():
    """base_case에서 shift한 만큼 tail_case에서 빼므로(또는 반대로), 이동 후에도
    4개 가중치 합은 항상 1.0이어야 한다."""
    from dataclasses import replace

    for shift in (-10.0, 0.0, 10.0):
        shifted = []
        for sc in SAMSUNG_SCENARIOS:
            if sc.name == "점진적 추격":
                shifted.append(replace(sc, weight=sc.weight + shift / 100))
            elif sc.name == "가격전쟁":
                shifted.append(replace(sc, weight=sc.weight - shift / 100))
            else:
                shifted.append(sc)
        assert sum(sc.weight for sc in shifted) == pytest.approx(1.0)


def test_probability_weight_sensitivity_higher_base_weight_shifts_value_toward_base_case():
    """점진적 추격(base_case) 적정가가 가격전쟁(tail_case) 적정가보다 높으므로,
    base_case 비중을 늘리면 최종 적정가도 올라가야 한다."""
    rows = probability_weight_sensitivity(SAMSUNG_BOOK_VALUE, SAMSUNG_SCENARIOS, shift_pct_pt=10.0)
    by_weight = {round(r["base_case_weight_pct"]): r["value"] for r in rows}
    assert by_weight[60] > by_weight[50] > by_weight[40]


def test_build_scenario_rationale_context_returns_full_shape():
    samsung = {"book_value": SAMSUNG_BOOK_VALUE}
    hynix = {"book_value": SK_HYNIX_BOOK_VALUE}
    ctx = build_scenario_rationale_context(samsung, hynix, SAMSUNG_SCENARIOS, SK_HYNIX_SCENARIOS)

    assert ctx["scenario_rationale_available"] is True
    assert len(ctx["scenario_rationale_rows"]) == 4
    assert len(ctx["samsung_probability_sensitivity_rows"]) == 3
    assert len(ctx["hynix_probability_sensitivity_rows"]) == 3
    assert "추정한 값이 아니다" in ctx["scenario_rationale_disclosure"]
