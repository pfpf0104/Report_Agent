"""워크포워드 백테스트 엔진 — 순수 함수.

포트폴리오 구성(app/computation/portfolio/)이 "이 시점에 어떤 비중을 가져야
하는가"를 답한다면, 이 모듈은 "그 비중을 여러 리밸런싱 시점에 반복 적용하면
실제로 어떤 수익률 경로가 나오는가"를 답한다. GIPS 성과표(risk/gips.py)가
요구하는 월간 수익률 시계열은 이 엔진의 출력이 채운다.

## 워크포워드 원칙

각 리밸런싱 시점 t에서:
  1. t 시점까지의(t 이후는 안 보이는) 가격 이력으로만 비중을 정한다 — look-ahead
     방지. 호출부가 넘기는 weight_fn이 이 책임을 진다(point_in_time.visible_as_of
     사용은 호출부 몫).
  2. t~t+1 구간의 실제 수익률로 자산가치가 변한다.
  3. 리밸런싱 비용(거래비용)을 자산가치에서 차감한다 — 비용 없는 백테스트는
     체계적으로 성과를 부풀린다.
  4. t+1의 실제 비중(가격 변화로 표류한 값)이 다음 리밸런싱의 "현재 비중"이
     된다 — 매번 목표 비중으로 리셋하지 않는다(그러면 표류 비용이 사라진다).

## 이 엔진이 하지 않는 것

신호 생성(어떤 자산에 얼마나 배분할지 "판단")은 weight_fn 콜백의 책임이다.
이 모듈은 DB도, 신호도 모른다 — 가격 행렬과 비중 함수만 받아 자산가치 경로를
만든다. 신호가 합성이든 실측이든 이 엔진의 정확성과는 무관하다(2026-08 기준
CallRank의 섹터 랭킹 신호는 sector_embeddings.py가 아직 합성이라, 이 엔진으로
낸 백테스트 결과는 "코드가 워크포워드/비용을 올바르게 적용하는지"의 검증이지
"실제로 유효한 전략인지"의 검증이 아니다 — 후자는 실신호 연동 후에 유효하다).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Callable

import numpy as np

from app.computation.portfolio.costs import CostModel, rebalance_cost

# weight_fn(as_of, price_history) -> weights(자산 순서는 price_history와 동일)
# price_history[asset_index] = as_of 이전(포함) 가격 리스트, 오름차순.
WeightFn = Callable[[date, list[list[float]]], np.ndarray | None]


@dataclass(frozen=True)
class RebalanceEvent:
    """한 리밸런싱 구간(이전 리밸런싱일 ~ 이번 리밸런싱일)의 결과."""

    as_of: date
    weights: np.ndarray  # 이 시점에 정한 목표 비중(비용 차감 전)
    period_return: float  # 다음 리밸런싱까지의 포트폴리오 수익률(비용 차감 후, 소수)
    turnover: float
    cost: float
    drifted_weights: np.ndarray  # 다음 리밸런싱 직전 실제 비중(가격 변화로 표류)


@dataclass
class BacktestResult:
    events: list[RebalanceEvent] = field(default_factory=list)

    @property
    def monthly_returns(self) -> list[tuple[date, float]]:
        """gips.build_gips_table이 바로 받을 수 있는 형식."""
        return [(e.as_of, e.period_return) for e in self.events]

    @property
    def cumulative_return(self) -> float:
        """전 구간 누적 수익률(복리, 소수)."""
        value = 1.0
        for e in self.events:
            value *= 1.0 + e.period_return
        return value - 1.0

    @property
    def total_cost(self) -> float:
        return sum(e.cost for e in self.events)


def _period_return(prev_weights: np.ndarray, period_asset_returns: np.ndarray) -> tuple[float, np.ndarray]:
    """비중과 자산별 기간수익률로 포트폴리오 수익률과 표류 후 비중을 낸다.

    표류 비중 계산: 자산 i의 가치는 w_i * (1+r_i)로 늘고, 포트폴리오 전체 가치는
    Σ w_i*(1+r_i)로 는다. 표류 비중 = 개별 자산가치 / 전체 자산가치.
    """
    grown = prev_weights * (1.0 + period_asset_returns)
    portfolio_growth = float(grown.sum())
    period_return = portfolio_growth - 1.0
    drifted = grown / portfolio_growth if portfolio_growth != 0 else prev_weights
    return period_return, drifted


def run_backtest(
    dates: list[date],
    prices: list[list[float]],
    weight_fn: WeightFn,
    cost_model: CostModel,
    *,
    initial_weights: np.ndarray | None = None,
) -> BacktestResult:
    """가격 행렬과 비중 함수로 워크포워드 백테스트를 실행한다.

    dates: 리밸런싱 시점(오름차순, 예: 매월 말). 최소 2개 필요(마지막은 종료
        시점으로만 쓰이고 그 자체는 리밸런싱하지 않는다 — n개 날짜에서 n-1개
        구간이 나온다).
    prices: prices[asset_index][date_index] = 그 자산의 그 날짜(dates[date_index])
        종가. 자산 순서는 weight_fn이 반환하는 비중과 일치해야 한다. 결측이나
        점프가 있으면 weight_fn이 판단해 None을 반환할 수 있다(그 구간은
        직전 비중을 유지하고 리밸런싱하지 않는다 — 거래비용도 0).
    weight_fn: 각 리밸런싱 시점에 목표 비중을 정하는 콜백. 자산 수만큼의
        배열을 반환하거나, 판단 불가하면 None(직전 비중 유지).
    initial_weights: 첫 리밸런싱 이전의 "현재 비중". 생략 시 동일가중.
    """
    n_assets = len(prices)
    if n_assets == 0:
        raise ValueError("자산이 하나도 없다")
    if any(len(p) != len(dates) for p in prices):
        raise ValueError("prices의 각 자산 길이는 dates 길이와 같아야 한다")
    if len(dates) < 2:
        raise ValueError("리밸런싱 시점이 최소 2개 필요하다(구간이 하나도 안 나온다)")

    current_weights = (
        np.asarray(initial_weights, dtype=float) if initial_weights is not None else np.full(n_assets, 1.0 / n_assets)
    )
    result = BacktestResult()

    for i in range(len(dates) - 1):
        as_of = dates[i]
        history = [prices[a][: i + 1] for a in range(n_assets)]

        target = weight_fn(as_of, history)
        if target is None:
            target = current_weights  # 판단 불가 — 리밸런싱하지 않는다(비용 0)
        else:
            target = np.asarray(target, dtype=float)
            if len(target) != n_assets:
                raise ValueError(f"weight_fn이 반환한 비중 길이({len(target)})가 자산 수({n_assets})와 다르다")

        cost = rebalance_cost(target, current_weights, cost_model)
        turnover = float(np.abs(target - current_weights).sum()) / 2.0

        asset_returns = np.array(
            [prices[a][i + 1] / prices[a][i] - 1.0 for a in range(n_assets)]
        )
        gross_return, drifted = _period_return(target, asset_returns)
        # 비용은 리밸런싱 시점에 자산가치에서 차감되므로 그 구간 수익률에서 뺀다.
        net_return = gross_return - cost

        result.events.append(
            RebalanceEvent(
                as_of=as_of,
                weights=target,
                period_return=net_return,
                turnover=turnover,
                cost=cost,
                drifted_weights=drifted,
            )
        )
        current_weights = drifted

    return result
