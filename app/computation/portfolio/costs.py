"""거래비용 모델 — 리밸런싱이 값어치를 하는지 판단한다.

신호가 좋아도 거래비용이 초과수익을 먹으면 리밸런싱은 손해다. 이 모듈은 그
손익분기를 계산한다.

## 비용 구조

    편도비용 = 스프레드/2 + 시장충격

  - **스프레드**: 호가 스프레드의 절반을 지불한다고 본다(중간가 대비).
  - **시장충격**: 거래량 대비 주문 크기에 따라 가격이 밀리는 정도.
    제곱근 법칙 impact = k·√(참여율) 을 쓴다 — 업계에서 널리 쓰이는 근사이며,
    선형 모형보다 대형 주문의 비용을 현실적으로 잡는다.

## 계수를 이 프로젝트가 정하지 않는 이유

k(충격계수)와 스프레드는 시장·종목·시점마다 다르다. 임의의 기본값을 코드에
박아두면 그 숫자가 근거처럼 보이게 된다 — 이 프로젝트는 이미 난수를 성과처럼
제시했던 전례가 있어(MASTER_PLAN G2) 그 방식을 반복하지 않는다. 호출부가
명시적으로 넘기게 하고, 리포트에는 사용한 가정을 함께 표시한다.

## 단위 규약

  - 비중·수익률: 소수(0.01 = 1%)
  - 스프레드·비용: **bp**로 받고 내부에서 소수로 변환(1bp = 0.0001)
    파라미터명에 `_bps`를 붙여 단위를 명시한다 — 이 프로젝트가 겪은 퍼센트/bp
    혼동(MASTER_PLAN G13)을 반복하지 않기 위함이다.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

BP = 1e-4


@dataclass(frozen=True)
class CostModel:
    """거래비용 가정. 리포트에 그대로 표시할 수 있도록 값 객체로 둔다."""

    spread_bps: float
    impact_coefficient_bps: float = 0.0
    participation_rate: float = 0.0

    def __post_init__(self) -> None:
        if self.spread_bps < 0 or self.impact_coefficient_bps < 0:
            raise ValueError("비용 계수는 음수일 수 없다")
        if not 0.0 <= self.participation_rate <= 1.0:
            raise ValueError("참여율은 0과 1 사이여야 한다")

    @property
    def one_way_cost(self) -> float:
        """편도 비용률(소수). 스프레드 절반 + 제곱근 시장충격."""
        spread = self.spread_bps * BP / 2.0
        impact = self.impact_coefficient_bps * BP * np.sqrt(self.participation_rate)
        return float(spread + impact)

    def describe(self) -> str:
        return (
            f"스프레드 {self.spread_bps:.1f}bp, 충격계수 {self.impact_coefficient_bps:.1f}bp, "
            f"참여율 {self.participation_rate * 100:.1f}% → 편도 {self.one_way_cost * 1e4:.2f}bp"
        )


def rebalance_cost(new_weights, current_weights, model: CostModel) -> float:
    """리밸런싱 총비용(소수). 매수·매도 양쪽에 편도비용이 붙는다.

    회전율은 단방향 정의(½Σ|Δw|)이고 실제 거래는 사고 파는 양쪽에서 일어나므로,
    비용은 Σ|Δw| × 편도비용 = 회전율 × 2 × 편도비용 이다.
    """
    new = np.asarray(list(new_weights), dtype=float)
    cur = np.asarray(list(current_weights), dtype=float)
    if len(new) != len(cur):
        raise ValueError(f"길이가 다르다: new={len(new)}, current={len(cur)}")
    traded = float(np.abs(new - cur).sum())
    return traded * model.one_way_cost


@dataclass(frozen=True)
class RebalanceDecision:
    expected_gross_alpha: float
    cost: float
    turnover: float

    @property
    def net_alpha(self) -> float:
        return self.expected_gross_alpha - self.cost

    @property
    def is_worthwhile(self) -> bool:
        return self.net_alpha > 0

    def describe(self) -> str:
        verdict = "실행" if self.is_worthwhile else "보류"
        return (
            f"{verdict} — 기대 초과수익 {self.expected_gross_alpha * 1e4:.1f}bp, "
            f"비용 {self.cost * 1e4:.1f}bp, 순 {self.net_alpha * 1e4:+.1f}bp "
            f"(회전율 {self.turnover * 100:.1f}%)"
        )


def evaluate_rebalance(
    new_weights, current_weights, expected_gross_alpha: float, model: CostModel
) -> RebalanceDecision:
    """리밸런싱이 비용을 넘는 값어치를 하는지 판단한다.

    expected_gross_alpha: 이 리밸런싱으로 기대하는 **비용 전** 초과수익(소수).
        신호의 예측력에서 나오는 값이며, 이 모듈이 만들어내지 않는다.
    """
    from app.computation.portfolio.constraints import turnover as _turnover

    return RebalanceDecision(
        expected_gross_alpha=expected_gross_alpha,
        cost=rebalance_cost(new_weights, current_weights, model),
        turnover=_turnover(new_weights, current_weights),
    )


def breakeven_alpha(new_weights, current_weights, model: CostModel) -> float:
    """이 리밸런싱을 정당화하려면 최소 얼마의 초과수익이 필요한가(소수).

    기대 초과수익이 이 값을 밑돌면 거래하지 않는 것이 낫다.
    """
    return rebalance_cost(new_weights, current_weights, model)
