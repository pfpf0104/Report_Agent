from datetime import date

import numpy as np
import pytest

from app.computation.quant.ridge_sector_rank import (
    MIN_ELIGIBLE_SECTORS,
    aggregate_company_to_sector,
    build_callrank_context,
    ensemble_rank,
    rank_sectors,
    run_sector_ranking,
    score_raw_residual,
)
from app.db.base import SessionLocal


def test_aggregate_company_to_sector_averages_equally_and_skips_unknown_codes():
    scores = {"XLK_00": 1.0, "XLK_01": 3.0, "XLF_00": 10.0, "UNKNOWN_99": 999.0}
    sector_of = {"XLK_00": "Information Technology", "XLK_01": "Information Technology", "XLF_00": "Financials"}
    result = aggregate_company_to_sector(scores, sector_of)
    assert result == {"Information Technology": pytest.approx(2.0), "Financials": pytest.approx(10.0)}


def test_rank_sectors_returns_none_below_minimum():
    scores = {f"sector_{i}": float(i) for i in range(MIN_ELIGIBLE_SECTORS - 1)}
    assert rank_sectors(scores) is None


def test_rank_sectors_normalizes_by_top_score():
    scores = {"A": 10.0, "B": 5.0, "C": -2.0, "D": 1.0, "E": 0.5, "F": 2.0}
    ranked = rank_sectors(scores)
    assert ranked[0] == {"sector": "A", "raw_score": 10.0, "normalized_score": 1.0}
    assert ranked[1]["normalized_score"] == pytest.approx(0.5)
    assert [r["sector"] for r in ranked] == sorted(scores, key=lambda k: scores[k], reverse=True)


def test_rank_sectors_falls_back_to_denom_one_when_top_score_non_positive():
    """top_score<=0이면 나눗셈 기준을 1.0으로 둬 0으로 나누지 않는다."""
    scores = {chr(65 + i): -float(i) for i in range(MIN_ELIGIBLE_SECTORS)}
    ranked = rank_sectors(scores)
    top = ranked[0]
    assert top["raw_score"] == 0.0
    assert top["normalized_score"] == 0.0


def test_score_raw_residual_zero_mean_defaults_to_positive_sign():
    """평균 성분이 정확히 0이면 부호를 임의로 -1이 아니라 +1로 고정한다."""
    vectors = np.array([[1.0, -1.0, 1.0, -1.0]])
    result = score_raw_residual(vectors)
    assert result[0] > 0


def test_ensemble_rank_averages_normalized_scores_across_models():
    per_length = [
        [{"sector": "A", "raw_score": 1.0, "normalized_score": 1.0}, {"sector": "B", "raw_score": 0.5, "normalized_score": 0.5}],
        [{"sector": "A", "raw_score": 1.0, "normalized_score": 0.6}, {"sector": "B", "raw_score": 0.5, "normalized_score": 0.9}],
    ]
    ensembled = ensemble_rank(per_length)
    scores = {row["sector"]: row["score"] for row in ensembled}
    assert scores["A"] == pytest.approx(0.8)
    assert scores["B"] == pytest.approx(0.7)
    assert ensembled[0]["sector"] == "A"  # 0.8 > 0.7


@pytest.mark.parametrize(
    "as_of,leading_sector",
    [
        (date(2026, 1, 31), "Energy"),
        (date(2026, 3, 31), "Financials"),
        (date(2026, 7, 30), "Information Technology"),
        (date(2025, 12, 31), "Real Estate"),
    ],
)
def test_run_sector_ranking_actually_recovers_planted_leading_sector(as_of, leading_sector):
    """모듈 docstring이 요구하는 검증: 정답을 하드코딩해 리턴하는 게 아니라, 계산이
    실제로 심어둔 leading_sector를 1위로 찾아내는지 확인한다. 완전히 결정적 시드라
    플레이키하지 않다."""
    result = run_sector_ranking(as_of, leading_sector_seed=leading_sector)
    assert result["eligible"] is True
    assert result["normalized_direction"][0]["sector"] == leading_sector


def test_run_sector_ranking_full_universe_is_always_eligible():
    """실제 섹터 유니버스(11개 섹터)는 MIN_ELIGIBLE_SECTORS(6)를 항상 넘는다."""
    result = run_sector_ranking(date(2026, 7, 30))
    assert result["eligible"] is True
    assert len(result["normalized_direction"]) > MIN_ELIGIBLE_SECTORS


def test_build_callrank_context_smoke():
    db = SessionLocal()
    try:
        context = build_callrank_context(db, date(2026, 7, 30))
    finally:
        db.close()

    for key in (
        "cards",
        "ranking_rows",
        "cross_check_rows",
        "model_agreement",
        "what_and_why_cards",
        "workflow_steps",
        "performance_available",
        "cross_asset_available",
        "regime_available",
    ):
        assert key in context, f"{key} 누락"

    # 성과 이력 충족 여부는 DB 상태에 따라 달라진다 — 두 경로 모두 컨텍스트가
    # 완결돼 있는지만 확인한다(어느 쪽이든 렌더 가능해야 한다).
    if context["performance_available"]:
        assert context["gips_rows"]
        assert context["risk_metric_rows"]
    else:
        assert "gips_requirements" in context
        assert len(context["gips_requirements"]) == 3
