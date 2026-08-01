"""CallRank 랭킹 → 집행 가능한 포트폴리오 페이지 컨텍스트.

Phase 2 모듈들(weighting/constraints/costs)을 실제 리포트에 연결하는 계층이다.

## 데이터가 없으면 만들어내지 않는다

비중 산출에는 섹터 ETF 공분산이 필요하고, 공분산에는 가격 이력이 필요하다.
`fact_market_daily`가 비어 있으면 계산할 수 없다 — 그럴 때 **합성 공분산을 만들어
그럴듯한 비중을 보여주면 안 된다.** 이 프로젝트는 난수를 성과처럼 제시했던 전례가
있고(MASTER_PLAN G2) 그것을 제거하면서 "근거 없는 수치는 싣지 않는다"는 원칙을
세웠다. 여기서도 같다: 이력이 부족하면 비중 대신 **왜 비어 있는지**를 싣는다.

## 최소 이력 요건

공분산 추정은 관측치가 자산 수보다 충분히 많아야 안정적이다. 관측치가 자산 수에
가까우면 표본공분산이 특이(singular)에 가까워져 비중이 극단으로 튄다. 11개 섹터
ETF 기준으로 최소 1년(252 거래일)을 요구한다 — 자산 수의 약 20배로, 표본공분산이
합리적으로 안정되는 수준이다.
"""
from __future__ import annotations

from datetime import date

import numpy as np
from sqlalchemy.orm import Session

from app.computation.portfolio.constraints import (
    ConstraintSet,
    apply_constraints,
    relax_cap_to_feasible,
    turnover,
)
from app.computation.portfolio.costs import CostModel, evaluate_rebalance
from app.computation.portfolio.weighting import (
    apply_scores_as_tilt,
    equal_weight,
    risk_contribution_shares,
    risk_parity,
)
from app.computation.risk.metrics import returns_from_prices
from app.db.models.dim_asset import DimAsset
from app.db.models.fact_market_daily import FactMarketDaily
from app.db.point_in_time import visible_as_of

MIN_OBSERVATIONS = 252

# 운용 지침 예시. 실제 지침이 정해지면 이 값을 교체한다 — 지금은 리포트에
# 가정으로 함께 표시해 근거 없이 쓰이지 않도록 한다.
#
# max_sector_weight를 걸지 않는 이유: CallRank의 자산은 섹터 ETF 하나씩이라
# **자산 = 섹터**다. 섹터 상한은 종목 상한과 완전히 중복이므로 설정하지 않는다
# (넣으면 apply_constraints가 불필요하게 sectors 인자를 요구하기도 한다).
DEFAULT_CONSTRAINTS = ConstraintSet(max_weight=0.25)
DEFAULT_TILT_STRENGTH = 0.4

PENDING_TITLE = "비중 산출은 가격 이력이 쌓인 뒤에 표시한다"
PENDING_BODY = (
    "섹터 비중을 내려면 섹터 ETF 간 공분산이 필요하고, 공분산에는 가격 이력이 "
    f"필요하다. 현재 fact_market_daily에 최소 요건({MIN_OBSERVATIONS} 거래일)을 "
    "충족하는 섹터가 부족해 비중을 계산하지 않았다. 합성 공분산으로 그럴듯한 "
    "비중을 만들어 싣는 대신 비워 둔다 — 근거 없는 수치를 싣지 않는다는 원칙은 "
    "성과 페이지와 동일하다."
)

METHOD_CARDS = [
    {
        "title": "01 · 위험 기준 중립 비중",
        "body": "먼저 랭킹을 무시하고 리스크패리티로 중립 비중을 만든다. 자본이 아니라 위험을 균등 배분해, 변동성이 큰 섹터가 포트폴리오 위험을 지배하지 않게 한다.",
    },
    {
        "title": "02 · 랭킹으로 기울이기",
        "body": f"CallRank 점수로 중립 비중을 기울인다(강도 {DEFAULT_TILT_STRENGTH}). 점수만으로 비중을 정하면 1위 섹터에 몰빵하게 되고, 위험 비중만 쓰면 신호가 전혀 반영되지 않는다.",
    },
    {
        "title": "03 · 제약으로 집행 가능하게",
        "body": f"종목 상한 {DEFAULT_CONSTRAINTS.max_weight * 100:.0f}%를 걸어 집중을 제한한다. 상한 적용은 단순 절삭이 아니라 위반 자산을 고정하고 잔여 예산을 재배분하는 반복이다.",
    },
]


def _sector_price_history(
    db: Session, as_of: date, etf_codes: list[str]
) -> dict[str, list[float]]:
    """as_of 시점에 알 수 있었던 섹터 ETF 종가 이력(거래일 오름차순)."""
    history: dict[str, list[float]] = {}
    for code in etf_codes:
        asset = db.query(DimAsset).filter_by(code=code).first()
        if asset is None:
            continue
        rows = (
            visible_as_of(db.query(FactMarketDaily), FactMarketDaily, as_of)
            .filter(
                FactMarketDaily.asset_id == asset.asset_id,
                FactMarketDaily.trade_date <= as_of,
                FactMarketDaily.adj_close.isnot(None),
            )
            .order_by(FactMarketDaily.trade_date.asc())
            .all()
        )
        if rows:
            history[code] = [float(r.adj_close) for r in rows]
    return history


def build_portfolio_context(
    db: Session,
    as_of: date,
    ranking_rows: list[dict],
    etf_by_sector: dict[str, str],
    constraints: ConstraintSet = DEFAULT_CONSTRAINTS,
    tilt_strength: float = DEFAULT_TILT_STRENGTH,
    cost_model: CostModel | None = None,
) -> dict:
    """랭킹을 비중으로 바꾸는 페이지의 컨텍스트.

    ranking_rows: [{"sector": ..., "score": ...}, ...] (ridge_sector_rank 출력)
    etf_by_sector: 섹터명 → ETF 코드
    """
    sectors = [row["sector"] for row in ranking_rows]
    codes = [etf_by_sector.get(s) for s in sectors]
    known = [(s, c) for s, c in zip(sectors, codes) if c]

    history = _sector_price_history(db, as_of, [c for _, c in known])
    usable = [(s, c) for s, c in known if len(history.get(c, [])) >= MIN_OBSERVATIONS + 1]

    if len(usable) < 2:
        return {
            "portfolio_available": False,
            "portfolio_pending_title": PENDING_TITLE,
            "portfolio_pending_body": PENDING_BODY,
            "portfolio_method_cards": METHOD_CARDS,
            "portfolio_data_status": (
                f"요건 충족 섹터 {len(usable)}개 / 랭킹 섹터 {len(sectors)}개 "
                f"(최소 {MIN_OBSERVATIONS} 거래일 필요)"
            ),
        }

    # 공통 구간으로 정렬 — 자산마다 이력 길이가 다르면 짧은 쪽에 맞춘다.
    length = min(len(history[c]) for _, c in usable)
    returns = np.array([returns_from_prices(history[c][-length:]) for _, c in usable])
    covariance = np.cov(returns, ddof=1)

    neutral = risk_parity(covariance)
    score_by_sector = {row["sector"]: row["score"] for row in ranking_rows}
    scores = [score_by_sector[s] for s, _ in usable]
    tilted = apply_scores_as_tilt(neutral, scores, tilt_strength=tilt_strength)

    effective, relaxed = relax_cap_to_feasible(constraints, len(usable))
    final = apply_constraints(tilted, effective)

    shares = risk_contribution_shares(final, covariance)
    current = equal_weight(len(final))  # 직전 비중이 영속화되기 전까지 동일가중을 기준으로 본다
    model = cost_model or CostModel(spread_bps=5.0)
    decision = evaluate_rebalance(final, current, expected_gross_alpha=0.0, model=model)

    rows = [
        [
            sector,
            code,
            f"{score_by_sector[sector]:.3f}",
            f"{neutral[i] * 100:.1f}%",
            f"{final[i] * 100:.1f}%",
            f"{shares[i] * 100:.1f}%",
        ]
        for i, (sector, code) in enumerate(usable)
    ]

    return {
        "portfolio_available": True,
        "portfolio_method_cards": METHOD_CARDS,
        "portfolio_rows": rows,
        "portfolio_assumptions": (
            f"중립 비중: 리스크패리티 · 기울기 강도 {tilt_strength} · "
            f"종목 상한 {effective.max_weight * 100:.1f}%"
            + (
                f" (설정값 {constraints.max_weight * 100:.0f}%는 {len(usable)}개 자산에 "
                f"적용 불가라 1/n으로 완화 — 이 경우 모든 비중이 1/n으로 강제되어 "
                f"리스크패리티 결과가 반영되지 않는다)" if relaxed else ""
            )
            + f" · 공분산 추정 {length}거래일"
        ),
        "portfolio_cost_note": (
            f"동일가중에서 전환 시 회전율 {turnover(final, current) * 100:.1f}%, "
            f"예상 비용 {decision.cost * 1e4:.1f}bp ({model.describe()})"
        ),
        "portfolio_data_status": f"요건 충족 섹터 {len(usable)}개 / 랭킹 섹터 {len(sectors)}개",
    }
