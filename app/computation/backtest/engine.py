"""워크포워드 백테스트 엔진.

이 프로젝트에는 백테스트 성과를 하드코딩 문자열("42.1%", "Sharpe 2.05")로
리포트에 실었던 전례가 있다(MASTER_PLAN G2). 그 숫자를 지운 자리를 채우는
모듈이며, 따라서 설계 목표 1순위는 "빠른 백테스트"가 아니라 **결과가 조작·
오염되지 않았음을 구조적으로 보일 수 있는 백테스트**다.

## 룩어헤드를 구조적으로 막는 방법

가장 흔한 백테스트 오류는 미래 정보로 비중을 정하는 것이다. 규율로 막으려 하면
언젠가 깨진다. 이 엔진은 대신 **비중 함수에 미래를 아예 보여주지 않는다**:

    weights = weight_fn(t, returns_panel[:t])   # t기 수익률은 슬라이스에 없다

`returns_panel[t]`(t기에 실현된 수익률)는 비중을 정한 뒤에야 쓰인다. 비중 함수가
악의적이든 실수든 미래를 참조할 방법이 없다 — 배열에 존재하지 않기 때문이다.
`app/db/point_in_time.py`의 `visible_as_of()`가 DB 계층에서 하는 일을, 이 모듈은
계산 계층에서 한다.

## 시점 규약

`returns_panel[t]`는 **t기 동안 실현된** 자산별 수익률이다(t-1 기말 → t 기말).
따라서 t기를 보유할 비중은 t-1 기말, 즉 t기가 시작하기 직전에 확정돼야 한다.
엔진의 루프는 매 t마다 (1) 리밸런싱 판단 → (2) t기 수익 실현 → (3) 드리프트
순으로 진행해 이 순서를 강제한다.

## 비중 드리프트를 반영하는 이유

리밸런싱 사이에 비중은 고정돼 있지 않다. 오른 자산의 비중은 저절로 커진다.
이를 무시하고 "매 기간 목표비중 유지"로 계산하면 두 가지가 동시에 틀린다.

  - **수익률**: 실제로는 승자 비중이 커진 상태로 다음 기간을 맞는다(모멘텀 효과).
  - **회전율·비용**: 리밸런싱 시 실제 거래량은 목표비중 대비 *드리프트된* 비중의
    차이다. 직전 목표비중과 비교하면 거래량이 과소평가되고, 비용이 실제보다
    싸 보인다 — 즉 성과가 부풀려진다.

## 단위 규약

수익률·비중·비용 전부 **소수**(0.01 = 1%). 비용 모델만 bp로 받고 내부에서
변환한다(`portfolio/costs.py`). MASTER_PLAN G13의 퍼센트/bp 혼동을 반복하지 않는다.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Callable, Sequence

import numpy as np

from app.computation.portfolio.constraints import ConstraintSet, apply_constraints, turnover
from app.computation.portfolio.costs import CostModel, rebalance_cost

# 비중 함수 시그니처: (t, 그 시점까지 알려진 수익률 패널) -> 목표비중 또는 None.
# None은 "이 시점 정보로는 비중을 정할 수 없다 — 현재 비중을 유지하라"는 뜻이다.
WeightFn = Callable[[int, np.ndarray], Sequence[float] | None]


@dataclass(frozen=True)
class BacktestResult:
    """백테스트 결과. 리포트에 싣는 모든 숫자가 여기서 나온다.

    길이 규약: 시계열 배열(`returns`·`gross_returns`·`costs`·`turnovers`)의 길이는
    `returns_panel`의 행 수 n과 같고, `weights`는 (n, n_assets)로 **각 기간을
    실제로 보유한 비중**(리밸런싱 후·드리프트 전)이다.
    """

    dates: list[date]
    returns: np.ndarray  # 비용 차감 후 순수익률
    gross_returns: np.ndarray  # 비용 차감 전
    costs: np.ndarray  # 각 기간에 발생한 거래비용(소수)
    weights: np.ndarray  # (n, n_assets) 해당 기간 보유 비중
    turnovers: np.ndarray  # 각 기간 리밸런싱 회전율(단방향 ½Σ|Δw|)
    rebalance_indices: list[int]  # 실제로 비중이 바뀐 기간(요청했지만 건너뛴 것은 제외)

    @property
    def equity_curve(self) -> np.ndarray:
        """순수익률 기준 누적 성장 곡선. 시작값 1.0을 포함해 길이가 n+1이다."""
        return np.concatenate([[1.0], np.cumprod(1.0 + self.returns)])

    @property
    def total_cost(self) -> float:
        """기간 전체 누적 거래비용(소수). 단순합 — 복리 효과는 무시한 근사다."""
        return float(self.costs.sum())

    @property
    def total_turnover(self) -> float:
        return float(self.turnovers.sum())


def _validate_panel(returns_panel) -> np.ndarray:
    panel = np.asarray(returns_panel, dtype=float)
    if panel.ndim != 2:
        raise ValueError("returns_panel은 (기간 × 자산) 2차원이어야 한다")
    if panel.shape[0] == 0 or panel.shape[1] == 0:
        raise ValueError("returns_panel이 비어 있다")
    if not np.all(np.isfinite(panel)):
        raise ValueError("returns_panel에 NaN/inf가 있다 — 결측은 호출부에서 처리해야 한다")
    return panel


def _drift(weights: np.ndarray, period_returns: np.ndarray) -> np.ndarray:
    """한 기간 수익률을 반영해 비중이 저절로 이동한 결과.

    전액 손실(∑w(1+r) ≤ 0)이면 정규화가 불가능하므로 비중을 그대로 둔다 —
    이 지점에서 자본이 사라졌다는 사실은 수익률 시계열에 이미 기록돼 있다.
    """
    grown = weights * (1.0 + period_returns)
    total = grown.sum()
    if total <= 0:
        return weights
    return grown / total


def run_backtest(
    dates: Sequence[date],
    returns_panel,
    *,
    weight_fn: WeightFn,
    rebalance_indices: Sequence[int],
    cost_model: CostModel,
    constraints: ConstraintSet | None = None,
    sectors: list[str] | None = None,
    initial_weights: Sequence[float] | None = None,
) -> BacktestResult:
    """워크포워드 백테스트를 실행한다.

    dates: 각 기간의 **기말** 날짜. `returns_panel`의 행 수와 길이가 같아야 한다.
    returns_panel: (기간 × 자산) 기간수익률. `returns_panel[t]`는 t기에 실현된 값.
    weight_fn: `(t, returns_panel[:t]) -> 목표비중 | None`. 두 번째 인자에 t기
        수익률이 **포함되지 않는다**는 점이 이 엔진의 룩어헤드 방지 장치다.
    rebalance_indices: 리밸런싱을 시도할 기간 인덱스. `periodic_rebalance_indices()`
        로 만들 수 있다. t=0을 포함하면 초기 비중 설정이 첫 리밸런싱이 된다.
    initial_weights: 첫 리밸런싱 전까지 보유할 비중. 생략하면 동일비중.

    비중 함수가 None을 반환하면(이력 부족 등) 그 시점 리밸런싱은 건너뛰고 현재
    비중을 유지한다 — 임의의 대체 비중을 만들어내지 않는다.
    """
    panel = _validate_panel(returns_panel)
    n_periods, n_assets = panel.shape

    if len(dates) != n_periods:
        raise ValueError(f"dates({len(dates)})와 returns_panel 행 수({n_periods})가 다르다")

    rebalance_set = set(int(i) for i in rebalance_indices)
    out_of_range = [i for i in rebalance_set if not 0 <= i < n_periods]
    if out_of_range:
        raise ValueError(f"리밸런싱 인덱스가 범위를 벗어났다: {sorted(out_of_range)}")

    if initial_weights is None:
        w = np.full(n_assets, 1.0 / n_assets)
    else:
        w = np.asarray(list(initial_weights), dtype=float)
        if len(w) != n_assets:
            raise ValueError(f"initial_weights 길이({len(w)})가 자산 수({n_assets})와 다르다")
        if not np.isclose(w.sum(), 1.0):
            raise ValueError(f"initial_weights 합이 1이 아니다: {w.sum():.6f}")

    gross = np.zeros(n_periods)
    costs = np.zeros(n_periods)
    turnovers = np.zeros(n_periods)
    held = np.zeros((n_periods, n_assets))
    executed: list[int] = []

    for t in range(n_periods):
        if t in rebalance_set:
            # 핵심: 슬라이스가 t 미만까지만이라 t기 이후 수익률은 볼 수 없다.
            target = weight_fn(t, panel[:t])
            if target is not None:
                target_arr = np.asarray(list(target), dtype=float)
                if len(target_arr) != n_assets:
                    raise ValueError(
                        f"t={t}에서 weight_fn이 자산 수와 다른 길이({len(target_arr)})를 반환했다"
                    )
                if constraints is not None:
                    target_arr = apply_constraints(
                        target_arr, constraints, sectors=sectors, current_weights=w
                    )
                turnovers[t] = turnover(target_arr, w)
                costs[t] = rebalance_cost(target_arr, w, cost_model)
                w = target_arr
                executed.append(t)

        held[t] = w
        gross[t] = float(w @ panel[t])
        w = _drift(w, panel[t])

    return BacktestResult(
        dates=list(dates),
        returns=gross - costs,
        gross_returns=gross,
        costs=costs,
        weights=held,
        turnovers=turnovers,
        rebalance_indices=executed,
    )


def periodic_rebalance_indices(dates: Sequence[date], frequency: str) -> list[int]:
    """리밸런싱 시점 인덱스 — 각 기간(월/분기/연)의 **마지막** 관측일.

    frequency: "M"(월), "Q"(분기), "A"(연).

    마지막 관측일을 고르는 이유: 그 시점에 그 기간 전체의 정보가 확정된다.
    엔진 규약상 인덱스 t에서의 리밸런싱은 `returns_panel[:t]`만 보므로, 월말
    인덱스를 넘겨도 그 달 마지막 날 수익률을 미리 쓰는 일은 생기지 않는다.

    맨 앞 인덱스 0은 결과에 포함하지 않는다(첫 기간 기말은 아직 어떤 기간의
    끝도 아니다). 초기 비중을 첫날 세우고 싶다면 호출부에서 0을 추가한다.
    """
    if frequency not in ("M", "Q", "A"):
        raise ValueError(f"지원하지 않는 리밸런싱 주기: {frequency}")

    def key(d: date) -> tuple:
        if frequency == "M":
            return (d.year, d.month)
        if frequency == "Q":
            return (d.year, (d.month - 1) // 3)
        return (d.year,)

    indices: list[int] = []
    for i in range(len(dates) - 1):
        if key(dates[i]) != key(dates[i + 1]):
            indices.append(i)
    return indices


def buy_and_hold(weights: Sequence[float]) -> WeightFn:
    """고정 목표비중을 반환하는 비중 함수 — 벤치마크·대조군용."""
    fixed = list(weights)

    def _fn(t: int, history: np.ndarray) -> Sequence[float]:
        return fixed

    return _fn


def from_covariance(
    builder: Callable[[np.ndarray], Sequence[float]], min_observations: int
) -> WeightFn:
    """수익률 이력에서 공분산을 추정해 비중을 만드는 비중 함수로 감싼다.

    관측치가 `min_observations` 미만이면 None을 반환해 리밸런싱을 건너뛴다 —
    표본이 모자란 공분산으로 만든 비중은 근거가 없고, 그런 비중으로 만든 성과는
    이 프로젝트가 없애기로 한 종류의 숫자다(MASTER_PLAN G2).
    """

    def _fn(t: int, history: np.ndarray) -> Sequence[float] | None:
        if len(history) < min_observations:
            return None
        return builder(history)

    return _fn
