"""Macro Regime Observations(4번째 리포트) 컨텍스트 빌더 스모크 테스트.

이 빌더는 새 계산을 하지 않고(dashboard_context.py docstring 참고) 세 전략
리포트·레짐·크로스에셋 컨텍스트를 재배열만 한다 — 따라서 여기서 검증할 것은
"셋을 합치는 배선이 실제로 되는가"이지 각 구성요소의 계산 정확성이 아니다
(그건 test_ridge_sector_rank.py/test_duration_controller.py/
test_residual_income_model.py/test_regime_classifier.py/test_cross_asset.py가
이미 담당한다).
"""
from datetime import date

from app.computation.regime.dashboard_context import build_macro_regime_context
from app.db.base import SessionLocal


def test_build_macro_regime_context_smoke():
    db = SessionLocal()
    try:
        context = build_macro_regime_context(db, date(2026, 7, 30))
    finally:
        db.close()

    assert context["as_of"] == "2026-07-30"
    assert "regime_available" in context
    assert "cross_asset_available" in context
    assert len(context["strategy_summaries"]) == 3

    titles = {s["title"] for s in context["strategy_summaries"]}
    assert titles == {
        "CallRank (미국 섹터)",
        "MetroGuard (한국 채권 듀레이션)",
        "밸류에이션 (한국 반도체 RIM)",
    }


def test_build_macro_regime_context_strategy_cards_are_not_empty():
    """각 전략의 headline 카드가 실제로 채워져 있어야 한다 — 배선이 끊겨
    빈 리스트만 넘어오는 회귀를 잡는다."""
    db = SessionLocal()
    try:
        context = build_macro_regime_context(db, date(2026, 7, 30))
    finally:
        db.close()

    for strategy in context["strategy_summaries"]:
        assert len(strategy["cards"]) > 0, f"{strategy['title']}의 cards가 비어 있다"
        for card in strategy["cards"]:
            assert "label" in card
            assert "value" in card


def test_build_macro_regime_context_reuses_regime_and_cross_asset_keys():
    """regime_report_context/cross_asset_report_context의 키가 그대로
    최상위에 병합돼야 한다 — 템플릿이 이 키를 직접 참조한다."""
    db = SessionLocal()
    try:
        context = build_macro_regime_context(db, date(2026, 7, 30))
    finally:
        db.close()

    if context["regime_available"]:
        assert "regime_quadrant" in context
        assert "regime_cards" in context
    if context["cross_asset_available"]:
        assert "cross_asset_labels" in context
        assert "cross_asset_heatmap_chart_uri" in context
