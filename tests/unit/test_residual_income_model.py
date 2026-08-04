from datetime import date

import pytest

from app.computation.valuation.residual_income_model import (
    SAMSUNG_BOOK_VALUE,
    SAMSUNG_SCENARIOS,
    SK_HYNIX_BOOK_VALUE,
    SK_HYNIX_SCENARIOS,
    RimScenario,
    _rim_value_breakdown,
    build_valuation_context,
    compute_rim_value,
    cost_of_equity_sensitivity,
    probability_weighted_value,
)
from app.db.base import SessionLocal
from app.db.models.dim_asset import AssetType, DimAsset
from app.db.models.fact_financial_quarterly import FactFinancialQuarterly
from app.db.models.fact_market_daily import FactMarketDaily

# 첨부 밸류에이션 보고서 원문 값(오차 수십 원 이내로 검산 완료 — CHANGELOG 커밋 참고).
SAMSUNG_TARGETS = {"제한적 추격": 384793, "점진적 추격": 229640, "공격적 추격": 127096, "가격전쟁": 89791}
HYNIX_TARGETS = {"제한적 추격": 2914632, "점진적 추격": 1565808, "공격적 추격": 706372, "가격전쟁": 454656}


@pytest.mark.parametrize("targets,book_value,scenarios", [
    (SAMSUNG_TARGETS, SAMSUNG_BOOK_VALUE, SAMSUNG_SCENARIOS),
    (HYNIX_TARGETS, SK_HYNIX_BOOK_VALUE, SK_HYNIX_SCENARIOS),
])
def test_rim_values_match_reference_report(targets, book_value, scenarios):
    result = probability_weighted_value(book_value, scenarios)
    for row in result["rows"]:
        target = targets[row["scenario"]]
        assert row["value"] == pytest.approx(target, abs=100)  # 원 단위 반올림 오차 허용


def _cleanup_005930_000660(session):
    session.query(FactMarketDaily).filter(FactMarketDaily.asset_id.in_(
        session.query(DimAsset.asset_id).filter(DimAsset.code.in_(["005930", "000660"]))
    )).delete(synchronize_session=False)
    session.query(FactFinancialQuarterly).filter(FactFinancialQuarterly.asset_id.in_(
        session.query(DimAsset.asset_id).filter(DimAsset.code.in_(["005930", "000660"]))
    )).delete(synchronize_session=False)
    session.query(DimAsset).filter(DimAsset.code.in_(["005930", "000660"])).delete(synchronize_session=False)
    session.commit()


@pytest.fixture()
def db():
    session = SessionLocal()
    # setup에도 정리가 필요하다 — 실제 인제스천 job(ingest_korean_equity_prices
    # 등)이 이미 005930/000660으로 운영 데이터를 채워둔 상태에서 이 테스트가
    # 실행되면, "KIS 데이터 없음 → 폴백" 시나리오를 검증하려는 테스트가 실제로는
    # 실측 경로를 타 실패한다(2026-08 실측: 삼성전자 실제 현재가가 207,000원으로
    # 채워진 상태에서 재현됨).
    _cleanup_005930_000660(session)
    yield session
    _cleanup_005930_000660(session)
    session.close()


def test_build_valuation_context_falls_back_without_kis_data(db):
    context = build_valuation_context(db, date(2026, 7, 30))
    samsung_card = context["cards"][0]
    assert "208,500원" in samsung_card["caption"]
    assert "보고서 고정값" in samsung_card["caption"]


def test_build_valuation_context_prefers_real_kis_price(db):
    asset = DimAsset(asset_type=AssetType.EQUITY.value, code="005930", name_kr="삼성전자", currency="KRW")
    db.add(asset)
    db.commit()
    db.refresh(asset)
    db.add(FactMarketDaily(
        asset_id=asset.asset_id, trade_date=date(2026, 7, 30), knowledge_date=date(2026, 7, 30),
        close=220000, adj_close=220000, source="kis",
    ))
    db.commit()

    context = build_valuation_context(db, date(2026, 7, 30))
    samsung_card = context["cards"][0]
    assert "220,000원" in samsung_card["caption"]
    assert "KIS 실시간 시세" in samsung_card["caption"]


def test_build_valuation_context_falls_back_book_value_without_dart_data(db):
    context = build_valuation_context(db, date(2026, 7, 30))
    assert context["samsung"]["book_value"] == pytest.approx(SAMSUNG_BOOK_VALUE)
    assert "보고서 고정값" in context["samsung"]["book_value_source"]


def test_build_valuation_context_prefers_real_dart_bps(db):
    asset = DimAsset(asset_type=AssetType.EQUITY.value, code="005930", name_kr="삼성전자", currency="KRW")
    db.add(asset)
    db.commit()
    db.refresh(asset)
    db.add(FactFinancialQuarterly(
        asset_id=asset.asset_id, fiscal_year=2025, fiscal_quarter=4,
        knowledge_date=date(2026, 3, 31), bps=90000.0, source="dart",
    ))
    db.commit()

    context = build_valuation_context(db, date(2026, 7, 30))
    assert context["samsung"]["book_value"] == pytest.approx(90000.0)
    assert "DART 2025년" in context["samsung"]["book_value_source"]
    # SK하이닉스는 DART 데이터가 없으니 그대로 폴백을 써야 한다
    assert context["hynix"]["book_value"] == pytest.approx(SK_HYNIX_BOOK_VALUE)


def test_rim_value_breakdown_components_sum_to_compute_rim_value():
    """가치 구성 페이지가 쓰는 breakdown이 기존 compute_rim_value 스칼라 API와
    같은 계산 경로를 공유하는지(리팩터링으로 값이 갈라지지 않았는지) 확인한다."""
    scenario = SAMSUNG_SCENARIOS[1]  # 점진적 추격
    breakdown = _rim_value_breakdown(SAMSUNG_BOOK_VALUE, scenario)
    assert breakdown["book_value"] + breakdown["pv_excess_income"] + breakdown["pv_terminal_value"] == pytest.approx(
        breakdown["total"]
    )
    assert breakdown["total"] == pytest.approx(compute_rim_value(SAMSUNG_BOOK_VALUE, scenario))


def test_cost_of_equity_sensitivity_zero_delta_matches_base_value():
    scenario = SAMSUNG_SCENARIOS[1]
    rows = cost_of_equity_sensitivity(SAMSUNG_BOOK_VALUE, scenario, deltas_pct_pt=(0.0,))
    assert rows[0]["change_pct"] == pytest.approx(0.0)
    assert rows[0]["value"] == pytest.approx(compute_rim_value(SAMSUNG_BOOK_VALUE, scenario))


def test_cost_of_equity_sensitivity_is_monotonically_decreasing_in_r():
    """자기자본비용이 높을수록 초과이익 할인폭이 커져 적정가는 낮아져야 한다."""
    scenario = SAMSUNG_SCENARIOS[1]
    rows = cost_of_equity_sensitivity(SAMSUNG_BOOK_VALUE, scenario, deltas_pct_pt=(-1.0, -0.5, 0.0, 0.5, 1.0))
    values = [r["value"] for r in rows]
    assert values == sorted(values, reverse=True)


def test_rim_value_breakdown_terminal_value_undefined_when_r_equals_g():
    """r=g이면 (r-g)=0으로 나눠 잔여가치가 무한대/오류가 나야 정상이다 —
    CHECKLIST_ITEMS의 'r>g 성립 확인' 항목이 실제로 왜 필요한지 보여주는 경계 테스트."""
    degenerate = RimScenario(
        name="degenerate", weight=1.0, roe_path=(10, 10, 10, 10, 10), payout_path=(50, 50, 50, 50, 50),
        cost_of_equity=10.0, terminal_roe=10.0, terminal_growth=10.0,
    )
    with pytest.raises(ZeroDivisionError):
        _rim_value_breakdown(100.0, degenerate)


def test_build_valuation_context_includes_new_pages_data():
    db = SessionLocal()
    try:
        context = build_valuation_context(db, date(2026, 7, 30))
    finally:
        db.close()

    for key in (
        "cycle_scenario_cards", "value_composition_rows", "pbr_rows", "weight_donut_chart_uri",
        "cross_asset_available",
        "regime_available",
        "disclosure_available",
        "lineage_rows",
        "industry_available",
        "industry_structure_cards",
        "scenario_rationale_available",
        "scenario_rationale_rows",
        "samsung_probability_sensitivity_rows",
        "hynix_probability_sensitivity_rows",
    ):
        assert key in context, f"{key} 누락"

    assert len(context["value_composition_rows"]) == 2
    assert len(context["pbr_rows"]) == 2
    for company_key in ("samsung", "hynix"):
        company = context[company_key]
        assert company["roe_chart_uri"].startswith("data:image/png;base64,")
        assert len(company["risk_cards"]) == 3


def test_valuation_report_template_renders_industry_and_disclosure_pages():
    """Phase 4-2/4-3/4-4 신규 페이지가 실제로 렌더링되는지 — 컨텍스트 키만
    맞고 템플릿이 다른 이름을 참조하면 Jinja가 조용히 빈 문자열을 낸다."""
    from app.rendering.pdf_service import render_html

    db = SessionLocal()
    try:
        context = build_valuation_context(db, date(2026, 7, 30))
    finally:
        db.close()

    html = render_html("valuation/report.html", context)

    assert "INDUSTRY AND COMPETITION" in html
    assert "방법론 한계 및 공시" in html
    assert "핵심 수치의 출처·계산 경로" in html
    assert "SCENARIO PROBABILITY RATIONALE" in html
    if context["industry_micron_available"]:
        assert "마이크론" in html
    else:
        assert context["industry_micron_data_status"] in html
