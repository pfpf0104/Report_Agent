"""narrative.py — 관찰 서술이 인과 어휘를 쓰지 않는지, 값을 정확히 문장화하는지 검증.

DB가 필요 없다 — RegimeContext/CrossAssetContext를 직접 구성해 순수 함수로
테스트한다.
"""
from datetime import date

import pytest

from app.computation.regime.classifier import RegimeContext
from app.computation.regime.narrative import (
    assert_no_causal_language,
    build_observation_narrative,
)
from app.computation.risk.cross_asset import CrossAssetContext


def _regime(available=True, **overrides) -> RegimeContext:
    defaults = dict(
        available=available,
        quadrant="둔화 (Slowdown)",
        growth_accelerating=False,
        inflation_accelerating=False,
        growth_yoy_pct=-1.3,
        growth_yoy_pct_prior=-0.9,
        inflation_yoy_pct=3.7,
        inflation_yoy_pct_prior=4.3,
        as_of_month=date(2026, 6, 1),
        data_status="산업생산 2026-06-01 · CPI 2026-06-01 기준",
    )
    defaults.update(overrides)
    return RegimeContext(**defaults)


def _cross_asset(available=True, **overrides) -> CrossAssetContext:
    defaults = dict(
        available=available,
        codes=["A", "B"],
        labels=["자산A", "자산B"],
        correlation=[[1.0, 0.5], [0.5, 1.0]],
        n_observations=1000,
        period="2021-01-01 ~ 2026-01-01 (1000거래일)",
        data_status="공통 거래일 1000일 기준 실측 상관계수",
    )
    defaults.update(overrides)
    return CrossAssetContext(**defaults)


def test_assert_no_causal_language_passes_clean_text():
    assert_no_causal_language("이 조합이 '둔화' 국면으로 판정됐다.")  # 예외 없이 통과해야 한다


@pytest.mark.parametrize("bad_word", ["때문", "원인", "영향을 줬", "영향을 미쳤", "유발", "초래", "이끌었"])
def test_assert_no_causal_language_rejects_causal_words(bad_word):
    with pytest.raises(ValueError):
        assert_no_causal_language(f"이 지표가 {bad_word}다.")


def test_build_observation_narrative_unavailable_regime_and_cross_asset():
    ctx = build_observation_narrative(_regime(available=False), _cross_asset(available=False))
    assert ctx["narrative_available"] is False
    assert ctx["narrative_sentences"] == []


def test_build_observation_narrative_includes_regime_sentence_with_correct_values():
    ctx = build_observation_narrative(_regime(), _cross_asset(available=False))
    assert ctx["narrative_available"] is True
    sentence = ctx["narrative_sentences"][0]
    assert "2026년 06월" in sentence
    assert "-1.3%" in sentence
    assert "-0.9%" in sentence
    assert "+3.7%" in sentence
    assert "+4.3%" in sentence
    assert "둔화 (Slowdown)" in sentence
    assert "감속" in sentence


def test_build_observation_narrative_includes_correlation_sentence_for_each_pair():
    ctx = build_observation_narrative(_regime(available=False), _cross_asset())
    assert len(ctx["narrative_sentences"]) == 1  # 자산 2개 -> 쌍 1개
    sentence = ctx["narrative_sentences"][0]
    assert "자산A" in sentence
    assert "자산B" in sentence
    assert "+0.50" in sentence


def test_build_observation_narrative_never_contains_causal_language():
    """실제로 생성된 전체 문장 세트에 인과 어휘가 하나도 없는지 확인 —
    assert_no_causal_language를 우회해 조용히 통과하는 회귀를 잡는다."""
    from app.computation.regime.narrative import _FORBIDDEN_WORDS

    ctx = build_observation_narrative(_regime(), _cross_asset())
    for sentence in ctx["narrative_sentences"]:
        for word in _FORBIDDEN_WORDS:
            assert word not in sentence


def test_correlation_strength_description_for_various_magnitudes():
    from app.computation.regime.narrative import _describe_correlation_strength

    assert "거의 없는" in _describe_correlation_strength(0.05)
    assert "약한" in _describe_correlation_strength(0.2)
    assert "중간" in _describe_correlation_strength(0.45)
    assert "강한" in _describe_correlation_strength(0.8)
    assert "음(-)의" in _describe_correlation_strength(-0.5)
    assert "양(+)의" in _describe_correlation_strength(0.5)


def test_disclosure_mentions_correlation_is_not_causation():
    ctx = build_observation_narrative(_regime(), _cross_asset())
    assert "인과관계" in ctx["narrative_disclosure"]
