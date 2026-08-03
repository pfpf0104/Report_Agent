"""관찰 기반 서술(observation-based narrative) — MASTER_PLAN Phase 3-4 전반부.

"왜 이렇게 움직이는가"(인과관계)를 자동 도출하지 않는다. 이 모듈이 만드는
문장은 RegimeContext·CrossAssetContext가 이미 계산한 값(레짐 판정, 상관계수)
을 조합해 **관찰된 사실**을 서술할 뿐이다 — "무엇이 함께 일어났는가"이지
"무엇이 무엇을 일으켰는가"가 아니다.

이 구분이 중요한 이유는 이 프로젝트 전체가 지키는 G2 원칙(근거 없는 추측을
사실처럼 제시하지 않는다) 때문이다. 상관관계는 인과관계가 아니라는 통계학의
기본 경고를 코드 차원에서 강제한다 — 이 모듈이 만드는 모든 문장은 "관찰됐다"
류의 서술어로 끝나야 하고, "때문에"·"원인"·"영향을 줬다" 같은 인과 어휘를
쓰지 않는다(_FORBIDDEN_WORDS로 실제로 검사한다 — 코드 리뷰가 아니라 런타임
자체 검증).

역사적으로 비슷한 레짐이 있었는지 찾는 패턴 매칭(더 강한 주장이 될 수 있는
기능)은 별도 모듈 app/computation/regime/analog.py가 담당한다 — 관찰 서술과
신뢰 수준이 다른 기능을 한 엔진에 섞지 않는다.
"""
from __future__ import annotations

from app.computation.regime.classifier import RegimeContext
from app.computation.risk.cross_asset import CrossAssetContext

# 이 모듈이 만드는 문장에 나오면 안 되는 인과 어휘. 실수로라도 "때문에"류
# 표현이 템플릿 문자열에 섞여 들어가면 즉시 예외로 잡아낸다(assert_no_causal_
# language) — 리뷰에서 놓쳐도 런타임에서 걸러진다.
_FORBIDDEN_WORDS = ("때문", "원인", "영향을 줬", "영향을 미쳤", "유발", "초래", "이끌었")


def assert_no_causal_language(text: str) -> None:
    for word in _FORBIDDEN_WORDS:
        if word in text:
            raise ValueError(
                f"관찰 서술에 인과 어휘 '{word}'가 포함됐다 — narrative.py는 "
                "관찰된 사실만 서술해야 한다(모듈 docstring 참고)."
            )


def _regime_sentence(regime: RegimeContext) -> str | None:
    if not regime.available:
        return None
    direction_growth = "가속" if regime.growth_accelerating else "감속"
    direction_inflation = "가속" if regime.inflation_accelerating else "감속"
    sentence = (
        f"{regime.as_of_month.strftime('%Y년 %m월')} 기준, 산업생산 YoY는 "
        f"{regime.growth_yoy_pct:+.1f}%로 직전({regime.growth_yoy_pct_prior:+.1f}%) "
        f"대비 {direction_growth}했고, CPI YoY는 {regime.inflation_yoy_pct:+.1f}%로 "
        f"직전({regime.inflation_yoy_pct_prior:+.1f}%) 대비 {direction_inflation}했다 "
        f"— 이 조합이 '{regime.quadrant}' 국면으로 판정됐다."
    )
    assert_no_causal_language(sentence)
    return sentence


def _correlation_sentences(cross_asset: CrossAssetContext) -> list[str]:
    if not cross_asset.available:
        return []
    sentences = []
    n = len(cross_asset.labels)
    for i in range(n):
        for j in range(i + 1, n):
            corr = cross_asset.correlation[i][j]
            strength = _describe_correlation_strength(corr)
            sentence = (
                f"{cross_asset.period} 동안 {cross_asset.labels[i]}와(과) "
                f"{cross_asset.labels[j]}의 일간 수익률 상관계수는 {corr:+.2f}로, "
                f"{strength} 관계가 관찰됐다."
            )
            assert_no_causal_language(sentence)
            sentences.append(sentence)
    return sentences


def _describe_correlation_strength(corr: float) -> str:
    """상관계수 절대값 구간별 서술 — 이 자체도 관찰 서술이지 판단이 아니다
    (예: "강한 상관"은 통계적 관례상의 구간 명칭일 뿐, "왜 강한지"를
    설명하지 않는다)."""
    abs_corr = abs(corr)
    direction = "양(+)의" if corr > 0 else ("음(-)의" if corr < 0 else "무")
    if abs_corr < 0.1:
        strength = "거의 없는"
    elif abs_corr < 0.3:
        strength = "약한"
    elif abs_corr < 0.6:
        strength = "중간 수준의"
    else:
        strength = "강한"
    return f"{strength} {direction}" if abs_corr >= 0.1 else f"{strength}"


def build_observation_narrative(regime: RegimeContext, cross_asset: CrossAssetContext) -> dict:
    """report_context 계열과 동일한 dict-반환 컨벤션."""
    sentences = []
    regime_sentence = _regime_sentence(regime)
    if regime_sentence:
        sentences.append(regime_sentence)
    sentences.extend(_correlation_sentences(cross_asset))

    return {
        "narrative_available": len(sentences) > 0,
        "narrative_sentences": sentences,
        "narrative_disclosure": (
            "위 문장은 이미 계산된 레짐 판정·상관계수를 문장으로 옮긴 것이며, "
            "새로운 통계 검정을 수행하지 않는다. '관찰됐다'는 서술은 그 기간에 "
            "그런 수치가 나왔다는 사실만을 뜻하며, 한 자산의 움직임이 다른 "
            "자산을 일으켰다거나 향후에도 같은 관계가 유지된다는 뜻이 아니다. "
            "상관관계는 인과관계가 아니다."
        ),
    }
