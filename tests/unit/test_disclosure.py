"""disclosure/report_context.py — 필수 공시 페이지 + 데이터 계보 부록.

DB가 필요 없다 — 리포트 타입 문자열만 받는 정적 함수라 순수 함수로 테스트한다.
"""
import pytest

from app.computation.disclosure.report_context import build_disclosure_report_context


@pytest.mark.parametrize("report_type", ["callrank", "metroguard", "valuation"])
def test_build_disclosure_report_context_returns_full_shape(report_type):
    ctx = build_disclosure_report_context(report_type)

    assert ctx["disclosure_available"] is True
    assert len(ctx["disclosure_methodology_limitations"]) > 0
    assert len(ctx["disclosure_data_source_rows"]) > 0
    for row in ctx["disclosure_data_source_rows"]:
        assert len(row) == 2
    assert "disclosure_conflict_of_interest" in ctx
    assert "disclosure_disclaimer" in ctx
    assert len(ctx["lineage_rows"]) > 0
    for row in ctx["lineage_rows"]:
        assert len(row) == 3
    assert "lineage_point_in_time_note" in ctx


def test_disclaimer_is_shared_across_report_types():
    """면책 문구는 리포트 종류와 무관하게 동일해야 한다 — 각 리포트가 서로
    다른 법적 문구를 갖게 되는 회귀를 잡는다."""
    disclaimers = {
        build_disclosure_report_context(rt)["disclosure_disclaimer"]
        for rt in ("callrank", "metroguard", "valuation")
    }
    assert len(disclaimers) == 1


def test_callrank_discloses_synthetic_embeddings():
    ctx = build_disclosure_report_context("callrank")
    joined = " ".join(ctx["disclosure_methodology_limitations"])
    assert "합성" in joined
    assert any("sector_embeddings.py" in row[1] for row in ctx["lineage_rows"])


def test_metroguard_discloses_city_ai_fallback():
    ctx = build_disclosure_report_context("metroguard")
    joined = " ".join(ctx["disclosure_methodology_limitations"])
    assert "폴백" in joined or "합성" in joined
    assert any("city_ai_stub.py" in row[1] for row in ctx["lineage_rows"])


def test_valuation_discloses_qualitative_scenario_weights():
    ctx = build_disclosure_report_context("valuation")
    joined = " ".join(ctx["disclosure_methodology_limitations"])
    assert "정성적" in joined


def test_unknown_report_type_raises():
    with pytest.raises(KeyError):
        build_disclosure_report_context("nonexistent")
