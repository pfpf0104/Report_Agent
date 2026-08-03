"""4번째 리포트 Macro Regime Observations — 3개 전략(CallRank·MetroGuard·밸류에이션)을
한 레짐 프레임에서 조망한다 (MASTER_PLAN Phase 3-3).

지금까지 3-1(레짐 분류)·3-2(크로스에셋 상관행렬)는 기존 3개 리포트 각각에
공유 페이지로 붙어 있었다 — 각 리포트를 읽는 사람은 그 리포트 맥락에서
레짐·상관관계를 보지만, "지금 이 순간 3개 전략이 다 같이 어떤 그림을
그리는가"를 한 번에 보여주는 자리는 없었다. 이 리포트가 그 자리다.

## 무엇을 새로 계산하지 않는가

이 리포트는 새로운 계산을 하지 않는다 — regime/classifier.py, risk/cross_asset.py,
그리고 3개 전략 리포트가 이미 만든 headline 카드를 그대로 재사용해 한 문서에
모은다. 새 숫자를 만들어내는 게 아니라 이미 검증된 숫자들을 재배열하는
것이므로, 이 리포트 자체에 새로운 정확성 리스크는 없다 — 각 구성요소의
정확성은 이미 그 구성요소의 테스트가 보증한다.

## Phase 3-4: 관찰 서술 + 역사적 패턴 매칭

narrative.py(관찰 서술)와 analog.py(역사적 레짐 패턴 매칭)는 여기서 딱 한 번
classify_regime()/build_cross_asset_correlation()을 호출해 그 결과를
narrative/analog 양쪽에 그대로 넘긴다 — regime/report_context.py(dict 반환)를
거치지 않고 원본 dataclass(RegimeContext/CrossAssetContext)를 재사용해, 같은
값을 여러 번 다시 계산하지 않는다.
"""
from __future__ import annotations

from datetime import date

from sqlalchemy.orm import Session

from app.computation.fixed_income.duration_controller import build_metroguard_context
from app.computation.quant.ridge_sector_rank import build_callrank_context
from app.computation.regime.analog import build_analog_report_context
from app.computation.regime.classifier import classify_regime
from app.computation.regime.narrative import build_observation_narrative
from app.computation.regime.report_context import build_regime_report_context
from app.computation.risk.cross_asset import build_cross_asset_correlation, build_cross_asset_report_context
from app.computation.valuation.residual_income_model import build_valuation_context


def build_macro_regime_context(db: Session, as_of: date) -> dict:
    callrank = build_callrank_context(db, as_of)
    metroguard = build_metroguard_context(db, as_of)
    valuation = build_valuation_context(db, as_of)

    # 원본 dataclass는 한 번만 계산해 report_context(dict 변환)와
    # narrative/analog(dataclass 그대로 소비) 양쪽에 재사용한다.
    regime_ctx = classify_regime(db, as_of)
    cross_asset_ctx = build_cross_asset_correlation(db, as_of)

    regime = build_regime_report_context(db, as_of)
    cross_asset = build_cross_asset_report_context(db, as_of)
    narrative = build_observation_narrative(regime_ctx, cross_asset_ctx)
    analog = build_analog_report_context(db, as_of)

    strategy_summaries = [
        {
            "title": "CallRank (미국 섹터)",
            "cards": callrank.get("cards", []),
        },
        {
            "title": "MetroGuard (한국 채권 듀레이션)",
            "cards": metroguard.get("cards", []),
        },
        {
            "title": "밸류에이션 (한국 반도체 RIM)",
            "cards": valuation.get("cards", []),
        },
    ]

    return {
        "as_of": as_of.isoformat(),
        "strategy_summaries": strategy_summaries,
        **regime,
        **cross_asset,
        **narrative,
        **analog,
    }
