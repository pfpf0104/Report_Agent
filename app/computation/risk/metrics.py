"""성과·리스크 지표 — 순수 함수 모음.

전부 수익률 시계열(period return, 소수. 0.01 = +1%)을 입력으로 받는 순수 함수다.
DB나 외부 API에 의존하지 않으므로 실데이터가 채워지기 전에도 구현·검증이 가능하고,
데이터가 들어오면 그대로 붙는다.

## 규약

  - `returns`: 기간수익률 배열. 가격이 아니라 수익률이다(가격은 `returns_from_prices`).
  - `periods_per_year`: 연환산 계수. 일간=252, 주간=52, 월간=12, 연간=1.
  - 수익률·비율은 **소수**로 주고받는다(0.0345 = 3.45%). 퍼센트 변환은 표시 계층에서.
    이 프로젝트는 이미 퍼센트/bp 혼동으로 잠복 버그를 겪었으므로(MASTER_PLAN G13)
    계산 계층은 단위를 하나로 고정한다.
  - 표본 표준편차는 ddof=1(불편추정량)을 쓴다 — GIPS·업계 관행과 일치.

## 왜 직접 구현하는가

empyrical 같은 라이브러리를 쓸 수도 있지만, 기관 리포트에 싣는 숫자는 계산식이
문서에 드러나야 하고 검산 가능해야 한다. 각 함수는 docstring에 정의를 명시하고,
테스트는 손으로 계산 가능한 케이스로 검증한다.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import numpy as np

TRADING_DAYS_PER_YEAR = 252

# 이 값 미만의 변동성·분산은 0으로 취급한다.
#
# 정확히 `== 0`으로 비교하면 안 된다: 상수 수익률([0.01]*10)의 표본표준편차는
# 수학적으로 0이지만 부동소수점 연산에서는 6.3e-18 같은 값이 나온다. 그러면
# 0 나눗셈 가드가 통과돼 Sharpe가 2.0e+16 같은 쓰레기 값을 반환하고, 그 숫자가
# 그대로 리포트에 실린다(테스트로 실제 재현함).
#
# 1e-12는 실제 시장 변동성(연 1% = 0.01)보다 10자리 아래라 정상 데이터를 오탐할
# 여지가 없으면서, 부동소수점 노이즈(~1e-18)보다는 충분히 크다.
_NEGLIGIBLE = 1e-12


def _as_array(returns) -> np.ndarray:
    arr = np.asarray(list(returns), dtype=float)
    if arr.ndim != 1:
        raise ValueError("수익률은 1차원 시계열이어야 한다")
    return arr


def returns_from_prices(prices) -> np.ndarray:
    """가격 시계열 → 기간수익률. 길이가 1 줄어든다."""
    p = _as_array(prices)
    if len(p) < 2:
        return np.array([])
    if np.any(p[:-1] == 0):
        raise ValueError("0인 가격이 있어 수익률을 계산할 수 없다")
    return p[1:] / p[:-1] - 1.0


def cumulative_return(returns) -> float:
    """누적 수익률 = ∏(1+r) − 1."""
    r = _as_array(returns)
    if len(r) == 0:
        return 0.0
    return float(np.prod(1.0 + r) - 1.0)


def annualized_return(returns, periods_per_year: int = TRADING_DAYS_PER_YEAR) -> float:
    """기하평균 연환산 수익률 = (∏(1+r))^(ppy/n) − 1.

    산술평균×ppy가 아니라 기하평균을 쓴다 — 복리 효과를 반영해야 실제 투자자가
    경험한 수익률과 일치한다.
    """
    r = _as_array(returns)
    if len(r) == 0:
        return 0.0
    growth = float(np.prod(1.0 + r))
    if growth <= 0:
        # 전액 손실(누적 -100% 이하). 기하평균이 정의되지 않으므로 -100%로 본다.
        return -1.0
    return growth ** (periods_per_year / len(r)) - 1.0


def annualized_volatility(returns, periods_per_year: int = TRADING_DAYS_PER_YEAR) -> float:
    """연환산 변동성 = 표본표준편차(ddof=1) × √ppy."""
    r = _as_array(returns)
    if len(r) < 2:
        return 0.0
    vol = float(np.std(r, ddof=1) * np.sqrt(periods_per_year))
    return 0.0 if vol < _NEGLIGIBLE else vol


def downside_deviation(
    returns, target: float = 0.0, periods_per_year: int = TRADING_DAYS_PER_YEAR
) -> float:
    """하방편차 — target 미만 구간만의 변동성(연환산).

    분모를 전체 표본 수 n으로 나눈다(하방 관측치 수가 아니라). Sortino 원 정의이며,
    하방 관측이 드물수록 위험이 작게 나오는 성질을 유지한다.
    """
    r = _as_array(returns)
    if len(r) == 0:
        return 0.0
    shortfall = np.minimum(r - target, 0.0)
    dd = float(np.sqrt(np.sum(shortfall**2) / len(r)) * np.sqrt(periods_per_year))
    return 0.0 if dd < _NEGLIGIBLE else dd


def sharpe_ratio(
    returns, risk_free_rate: float = 0.0, periods_per_year: int = TRADING_DAYS_PER_YEAR
) -> float | None:
    """(연환산수익 − 무위험수익) / 연환산변동성.

    risk_free_rate는 **연율 소수**(0.03 = 3%). 변동성이 0이면 정의되지 않으므로
    None을 반환한다 — 0이나 inf로 뭉개면 리포트에 무의미한 숫자가 실린다.
    """
    vol = annualized_volatility(returns, periods_per_year)
    if vol < _NEGLIGIBLE:
        return None
    return (annualized_return(returns, periods_per_year) - risk_free_rate) / vol


def sortino_ratio(
    returns, target: float = 0.0, periods_per_year: int = TRADING_DAYS_PER_YEAR
) -> float | None:
    """(연환산수익 − target) / 하방편차. 하방편차가 0이면 None."""
    dd = downside_deviation(returns, target, periods_per_year)
    if dd < _NEGLIGIBLE:
        return None
    return (annualized_return(returns, periods_per_year) - target) / dd


@dataclass(frozen=True)
class DrawdownResult:
    max_drawdown: float  # 음수 (예: -0.23 = -23%)
    peak_index: int  # returns 기준 인덱스. -1이면 고점이 **기초**(투자 시작 시점)
    trough_index: int
    recovery_index: int | None  # 고점 회복 시점. 미회복이면 None


def max_drawdown(returns) -> DrawdownResult:
    """최대낙폭 — 고점 대비 최대 하락률과 그 구간.

    부의 경로에 **기초 자본 1.0을 포함**한다. 이걸 빼면 시작하자마자 하락하는
    구간의 낙폭이 실제보다 얕게 나온다: [-10%, -10%]는 1.0 → 0.81이므로 -19%가
    맞는데, cumprod만 쓰면 첫 고점이 0.9가 돼 -10%로 계산된다(롤링 낙폭 테스트에서
    실제로 잡힌 오류다). 기관 성과표의 MDD는 투자 시작 시점을 고점 후보로 본다.

    peak_index가 -1이면 고점이 기초 자본이라는 뜻이다 — 즉 관측 구간 안에서
    단 한 번도 원금을 넘긴 적이 없다.

    recovery_index는 낙폭 이후 **직전 고점을 회복한** 첫 시점이다. 관측 구간
    안에서 회복하지 못했으면 None — 이 구분이 중요하다. 회복 여부를 표시하지
    않으면 -30% 낙폭이 한 달 만에 회복됐는지 3년째 미회복인지 알 수 없다.
    """
    r = _as_array(returns)
    if len(r) == 0:
        return DrawdownResult(0.0, 0, 0, None)

    # index 0 = 기초(수익률 반영 전). 이후 인덱스는 returns 인덱스 + 1.
    wealth = np.concatenate([[1.0], np.cumprod(1.0 + r)])
    running_peak = np.maximum.accumulate(wealth)
    drawdowns = wealth / running_peak - 1.0

    trough = int(np.argmin(drawdowns))
    mdd = float(drawdowns[trough])

    if mdd == 0:
        return DrawdownResult(0.0, 0, 0, None)

    peak = int(np.argmax(wealth[: trough + 1]))
    peak_value = float(wealth[peak])

    recovery = None
    after = np.nonzero(wealth[trough + 1 :] >= peak_value)[0]
    if len(after) > 0:
        recovery = int(trough + 1 + after[0]) - 1

    return DrawdownResult(mdd, peak - 1, trough - 1, recovery)


def calmar_ratio(returns, periods_per_year: int = TRADING_DAYS_PER_YEAR) -> float | None:
    """연환산수익 / |최대낙폭|. 낙폭이 없으면 None."""
    mdd = max_drawdown(returns).max_drawdown
    if mdd == 0:
        return None
    return annualized_return(returns, periods_per_year) / abs(mdd)


def historical_var(returns, confidence: float = 0.95) -> float:
    """히스토리컬 VaR — 손실을 **양수**로 반환한다(0.031 = 3.1% 손실 가능).

    분포 가정 없이 과거 실현 분위수를 그대로 쓴다. 정규분포 가정(파라메트릭)은
    금융 수익률의 팻테일을 과소평가하므로 기본값으로 두지 않는다.
    """
    r = _as_array(returns)
    if len(r) == 0:
        return 0.0
    if not 0 < confidence < 1:
        raise ValueError("confidence는 0과 1 사이여야 한다")
    return float(-np.percentile(r, (1.0 - confidence) * 100.0))


def conditional_var(returns, confidence: float = 0.95) -> float:
    """CVaR(Expected Shortfall) — VaR을 초과하는 손실의 평균. 양수로 반환.

    VaR은 "그 선을 넘을 확률"만 말하고 넘었을 때 얼마나 잃는지는 말하지 않는다.
    꼬리위험을 보려면 CVaR을 함께 본다.
    """
    r = _as_array(returns)
    if len(r) == 0:
        return 0.0
    threshold = -historical_var(r, confidence)
    tail = r[r <= threshold]
    if len(tail) == 0:
        return float(-threshold)
    return float(-np.mean(tail))


def _align(portfolio, benchmark) -> tuple[np.ndarray, np.ndarray]:
    p, b = _as_array(portfolio), _as_array(benchmark)
    if len(p) != len(b):
        raise ValueError(f"길이가 다르다: portfolio={len(p)}, benchmark={len(b)}")
    return p, b


def tracking_error(
    portfolio, benchmark, periods_per_year: int = TRADING_DAYS_PER_YEAR
) -> float:
    """추적오차 = 초과수익(rp − rb)의 연환산 표준편차."""
    p, b = _align(portfolio, benchmark)
    if len(p) < 2:
        return 0.0
    te = float(np.std(p - b, ddof=1) * np.sqrt(periods_per_year))
    return 0.0 if te < _NEGLIGIBLE else te


def information_ratio(
    portfolio, benchmark, periods_per_year: int = TRADING_DAYS_PER_YEAR
) -> float | None:
    """(연환산 포트폴리오수익 − 연환산 벤치마크수익) / 추적오차."""
    p, b = _align(portfolio, benchmark)
    te = tracking_error(p, b, periods_per_year)
    if te < _NEGLIGIBLE:
        return None
    excess = annualized_return(p, periods_per_year) - annualized_return(b, periods_per_year)
    return excess / te


def beta(portfolio, benchmark) -> float | None:
    """cov(rp, rb) / var(rb). 벤치마크 분산이 0이면 None."""
    p, b = _align(portfolio, benchmark)
    if len(p) < 2:
        return None
    var_b = float(np.var(b, ddof=1))
    if var_b < _NEGLIGIBLE:
        return None
    return float(np.cov(p, b, ddof=1)[0, 1] / var_b)


def alpha(
    portfolio,
    benchmark,
    risk_free_rate: float = 0.0,
    periods_per_year: int = TRADING_DAYS_PER_YEAR,
) -> float | None:
    """Jensen's alpha (연율) = Rp − [Rf + β(Rb − Rf)]."""
    b_val = beta(portfolio, benchmark)
    if b_val is None:
        return None
    rp = annualized_return(portfolio, periods_per_year)
    rb = annualized_return(benchmark, periods_per_year)
    return rp - (risk_free_rate + b_val * (rb - risk_free_rate))
