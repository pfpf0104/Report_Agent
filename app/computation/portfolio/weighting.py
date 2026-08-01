"""비중 산출 — 랭킹을 실행 가능한 포트폴리오로 바꾸는 첫 단계.

CallRank는 "어느 섹터가 1위인가"까지만 말하고 "그래서 얼마씩 담는가"는 말하지
않는다. 이 모듈이 그 간극을 메운다.

## 제공하는 방식

  - `equal_weight`         — 1/n. 가장 단순하고, 추정오차에 강하다.
  - `inverse_volatility`   — 변동성 역수. 변동성이 큰 자산을 덜 담는다.
  - `risk_parity`          — 각 자산의 **위험 기여도**를 균등화(ERC).

## 왜 리스크패리티인가

동일가중은 "돈"을 균등 배분하지만 "위험"은 균등하지 않다. 주식과 채권을 50:50으로
담으면 자본은 반반이어도 포트폴리오 변동성의 대부분은 주식에서 온다. Bridgewater의
All Weather가 자본이 아니라 위험을 균등 배분하는 이유이며, 국민연금식 "위험한도 내
배분"과도 같은 발상이다(references/README.md).

## 단위 규약

수익률·변동성은 전부 **소수**(0.15 = 15%). 비중은 합이 1.0이 되는 소수.
metrics.py와 동일한 규약이며, 이 프로젝트가 겪은 퍼센트/bp 혼동(MASTER_PLAN G13)을
반복하지 않기 위해 계산 계층은 단위를 하나로 고정한다.
"""
from __future__ import annotations

import numpy as np

# ERC 수렴 판정. 위험기여도 최대편차가 이 값 미만이면 수렴으로 본다.
ERC_TOLERANCE = 1e-10
ERC_MAX_ITERATIONS = 10_000


def _validate(values: np.ndarray, name: str) -> None:
    if len(values) == 0:
        raise ValueError(f"{name}가 비어 있다")
    if np.any(~np.isfinite(values)):
        raise ValueError(f"{name}에 NaN/무한대가 있다")


def equal_weight(n_assets: int) -> np.ndarray:
    """1/n 동일가중."""
    if n_assets <= 0:
        raise ValueError("자산 수는 1 이상이어야 한다")
    return np.full(n_assets, 1.0 / n_assets)


def inverse_volatility(volatilities) -> np.ndarray:
    """변동성 역수 비중. w_i ∝ 1/σ_i.

    자산 간 상관을 무시하므로 리스크패리티의 근사다 — 상관이 전부 동일할 때만
    ERC와 일치한다.
    """
    vol = np.asarray(list(volatilities), dtype=float)
    _validate(vol, "변동성")
    if np.any(vol <= 0):
        raise ValueError("변동성은 양수여야 한다 — 0이면 비중이 발산한다")
    inv = 1.0 / vol
    return inv / inv.sum()


def portfolio_volatility(weights, covariance) -> float:
    """√(wᵀΣw)."""
    w = np.asarray(list(weights), dtype=float)
    cov = np.asarray(covariance, dtype=float)
    return float(np.sqrt(w @ cov @ w))


def risk_contributions(weights, covariance) -> np.ndarray:
    """자산별 위험 기여도. 합하면 포트폴리오 변동성이 된다.

    RC_i = w_i × (Σw)_i / σ_p  —  오일러 분해. 이 성질(합 = σ_p) 덕분에
    "이 자산이 전체 위험의 몇 %를 차지하는가"를 말할 수 있다.
    """
    w = np.asarray(list(weights), dtype=float)
    cov = np.asarray(covariance, dtype=float)
    sigma_p = portfolio_volatility(w, cov)
    if sigma_p == 0:
        return np.zeros_like(w)
    return w * (cov @ w) / sigma_p


def risk_parity(covariance, max_iterations: int = ERC_MAX_ITERATIONS) -> np.ndarray:
    """Equal Risk Contribution — 모든 자산의 위험 기여도를 같게 만드는 롱온리 비중.

    ## 알고리즘: 제곱근 감쇠 곱셈 갱신

        wᵢ ← wᵢ × √( (σ²ₚ/n) / (wᵢ·(Σw)ᵢ) )   후 정규화

    분산 기준 위험기여도 wᵢ(Σw)ᵢ 가 목표치 σ²ₚ/n 에 못 미치면 비중을 키우고,
    넘으면 줄인다. 수렴점에서 모든 wᵢ(Σw)ᵢ 가 같아지므로 곧 ERC다.

    **감쇠(√) 없는 순진한 고정점 wᵢ ← 1/(Σw)ᵢ 를 쓰면 안 된다.** 그 반복은
    수렴하지 않고 진동한다 — 무상관 자산에서 균등가중과 1/σ² 비중 사이를 왕복하다
    균등가중을 내놓는다(정답은 1/σ). 실제로 그렇게 구현했다가 테스트로 잡았다.
    √ 감쇠를 넣으면 같은 무상관 케이스에서 1회 반복만에 정확히 1/σ에 도달한다.

    2차 최적화 솔버(cvxpy 등)를 쓰지 않는 이유: 의존성을 늘리지 않고도 이 문제는
    닫힌 반복으로 안정적으로 풀리며, 기관 리포트에 실릴 계산은 식이 드러나 있어야
    검산 가능하기 때문이다.
    """
    cov = np.asarray(covariance, dtype=float)
    if cov.ndim != 2 or cov.shape[0] != cov.shape[1]:
        raise ValueError("공분산은 정방행렬이어야 한다")
    _validate(cov.ravel(), "공분산")

    n = cov.shape[0]
    if n == 1:
        return np.array([1.0])
    if np.any(np.diag(cov) <= 0):
        raise ValueError("공분산 대각(분산)은 양수여야 한다")

    fallback = inverse_volatility(np.sqrt(np.diag(cov)))

    w = equal_weight(n)
    for _ in range(max_iterations):
        marginal = cov @ w
        # 분산 기준 위험기여도. 합하면 σ²ₚ가 된다.
        rc_var = w * marginal
        portfolio_var = float(rc_var.sum())

        if portfolio_var <= 0 or np.any(rc_var <= 0):
            # 공분산이 양정치가 아니거나(완전상관 등) 수치적으로 불안정한 경우.
            # 상관을 무시하는 근사지만 항상 정의되는 역변동성으로 후퇴한다.
            return fallback

        target = portfolio_var / n
        w_new = w * np.sqrt(target / rc_var)
        w_new /= w_new.sum()

        rc = risk_contributions(w_new, cov)
        if rc.max() - rc.min() < ERC_TOLERANCE:
            return w_new
        w = w_new

    return w


def apply_scores_as_tilt(base_weights, scores, tilt_strength: float = 0.5) -> np.ndarray:
    """랭킹 점수로 기준 비중을 기울인다(신호 반영).

    base_weights: 위험 기반 중립 비중(risk_parity 등)
    scores: 자산별 신호 점수. 높을수록 선호.
    tilt_strength: 0이면 기준 비중 그대로, 1이면 점수 비중을 최대로 반영.

    CallRank 같은 랭킹 신호를 **위험 구조를 무시하지 않으면서** 반영하기 위한 것이다.
    점수만으로 비중을 정하면 1위 섹터에 몰빵하게 되고, 위험 기반 비중만 쓰면 신호가
    전혀 반영되지 않는다.
    """
    base = np.asarray(list(base_weights), dtype=float)
    s = np.asarray(list(scores), dtype=float)
    if len(base) != len(s):
        raise ValueError(f"길이가 다르다: base={len(base)}, scores={len(s)}")
    if not 0.0 <= tilt_strength <= 1.0:
        raise ValueError("tilt_strength는 0과 1 사이여야 한다")
    _validate(s, "점수")

    # 점수를 양수 비중으로: 최솟값 기준 이동 후 정규화. 전부 같으면 균등.
    shifted = s - s.min()
    if shifted.sum() == 0:
        score_weights = equal_weight(len(s))
    else:
        score_weights = shifted / shifted.sum()

    tilted = (1.0 - tilt_strength) * base + tilt_strength * score_weights
    return tilted / tilted.sum()
