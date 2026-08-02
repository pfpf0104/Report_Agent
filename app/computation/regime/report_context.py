"""레짐 분류 결과 → 리포트 컨텍스트. 3개 리포트(CallRank·MetroGuard·밸류에이션)가 공유한다.

app/computation/risk/cross_asset.py와 나란히 Phase 3의 "3개 리포트를 잇는"
공유 페이지 중 하나다 — cross_asset이 "자산들이 실제로 같이 움직였는가"를
보여준다면, 이 페이지는 "지금이 어떤 거시 국면인가"를 보여준다.
"""
from __future__ import annotations

from datetime import date

from sqlalchemy.orm import Session

from app.computation.regime.classifier import classify_regime
from app.computation.regime.reference_indicators import build_reference_indicator_rows

# 사분면별 해석 문구 — 페이지에서 판정 결과와 함께 보여준다. 이 프로젝트가
# 이 국면에서 무엇을 하라고 처방하지는 않는다(그건 이 리포트들의 실제
# 전략 로직이 아직 신호로 연결돼 있지 않음, G3/G4 참고) — "이 국면이
# 통상 무엇을 뜻하는가"라는 일반론만 서술한다.
QUADRANT_DESCRIPTIONS = {
    "과열 (Overheating)": "성장·인플레이션이 함께 가속하는 국면. 통상 명목 자산(원자재·주식)이 우호적이고 채권에는 비우호적이다.",
    "골디락스 (Goldilocks)": "성장은 가속하고 인플레이션은 감속하는 국면. 통상 주식에 가장 우호적이다.",
    "스태그플레이션 (Stagflation)": "성장은 감속하고 인플레이션은 가속하는 국면. 통상 대부분의 전통 자산군에 비우호적이다.",
    "둔화 (Slowdown)": "성장·인플레이션이 함께 감속하는 국면. 통상 채권에 우호적이고 주식에는 방어적 접근이 필요하다.",
}


def build_regime_report_context(db: Session, as_of: date) -> dict:
    """report_context 계열과 동일한 dict-반환 컨벤션 — Jinja 템플릿에 그대로 풀어 쓴다."""
    ctx = classify_regime(db, as_of)
    if not ctx.available:
        return {
            "regime_available": False,
            "regime_data_status": ctx.data_status,
        }

    cards = [
        {
            "label": "성장 판정 지표(산업생산 YoY)",
            "value": f"{ctx.growth_yoy_pct:+.2f}%",
            "caption": ("가속" if ctx.growth_accelerating else "감속")
            + f" · 직전 {ctx.growth_yoy_pct_prior:+.2f}%",
            "tone": "up" if ctx.growth_accelerating else "down",
        },
        {
            "label": "인플레 판정 지표(CPI YoY)",
            "value": f"{ctx.inflation_yoy_pct:+.2f}%",
            "caption": ("가속" if ctx.inflation_accelerating else "감속")
            + f" · 직전 {ctx.inflation_yoy_pct_prior:+.2f}%",
            "tone": "up" if ctx.inflation_accelerating else "down",
        },
    ]

    return {
        "regime_available": True,
        "regime_quadrant": ctx.quadrant,
        "regime_cards": cards,
        "regime_description": QUADRANT_DESCRIPTIONS.get(ctx.quadrant, ""),
        "regime_as_of_month": ctx.as_of_month.strftime("%Y년 %m월") if ctx.as_of_month else "",
        "regime_data_status": ctx.data_status,
        "regime_indicator_rows": [
            ["산업생산(성장 판정 지표)",
             f"{ctx.growth_yoy_pct:+.2f}%", f"{ctx.growth_yoy_pct_prior:+.2f}%",
             "가속" if ctx.growth_accelerating else "감속"],
            ["CPI(인플레 판정 지표)",
             f"{ctx.inflation_yoy_pct:+.2f}%", f"{ctx.inflation_yoy_pct_prior:+.2f}%",
             "가속" if ctx.inflation_accelerating else "감속"],
        ],
        "regime_disclosure": (
            "이 판정은 산업생산·CPI 단 2개 지표의 YoY 변화율 추세 방향(직전 대비 "
            "가속/감속)만으로 결정된다. 시장 컨센서스 예상치 대비 서프라이즈가 "
            "아니라 실측치 자체의 추세이며, 전통적 All Weather 프레임의 '예상 대비' "
            "정의와는 다르다. 인과관계나 향후 자산 성과를 예측하지 않는다."
        ),
        "regime_reference_rows": build_reference_indicator_rows(db, as_of),
    }
