"""롤링 분석 — 성과·리스크가 **시간에 따라 어떻게 변했는지** 본다.

전체 구간 하나의 Sharpe는 "이 전략이 어떤 국면에서 벌었고 어떤 국면에서 잃었나"를
숨긴다. 2020년에 몰아서 벌고 이후 3년간 잃은 전략과, 매년 꾸준히 번 전략의 전구간
Sharpe가 같을 수 있다. 기관 리포트가 롤링 차트를 싣는 이유가 이것이고, 이 모듈이
그 입력을 만든다.

## 정렬 규약 — NaN 패딩을 쓰지 않는 이유

롤링 값은 관측치가 `window`개 모여야 처음 생긴다. 흔한 구현은 앞쪽을 NaN으로 채워
길이를 맞추지만, 이 프로젝트에서는 그 NaN이 차트·표를 거쳐 리포트에 "0.00"으로
찍힐 위험이 있다(실제로 3년 미만 이력에서 GIPS 표준편차가 0.00%로 나올 뻔한 전례가
있어 `risk/gips.py`는 None을 반환하도록 만들었다).

그래서 여기서는 **값이 존재하는 구간만** 반환하고, 각 값이 어느 시점의 것인지를
`end_indices`로 함께 준다. 길이를 맞추는 책임은 표시 계층에 넘긴다.

## None의 의미

`values`의 원소가 None이면 "그 창에서는 지표가 수학적으로 정의되지 않는다"는
뜻이다(예: 변동성 0인 구간의 Sharpe). 0이 아니다. 표시 계층은 "—"로 그려야 한다.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Callable, Sequence

import numpy as np

from app.computation.risk.metrics import (
    TRADING_DAYS_PER_YEAR,
    _NEGLIGIBLE,
    annualized_volatility,
    max_drawdown,
    sharpe_ratio,
)

# 흔히 쓰는 12개월 창. 일간 데이터면 252, 월간 데이터면 12를 쓴다.
MONTHS_PER_YEAR = 12
DEFAULT_DAILY_WINDOW = TRADING_DAYS_PER_YEAR


@dataclass(frozen=True)
class RollingSeries:
    """롤링 지표 결과.

    end_indices[i]는 values[i]를 만든 창의 **마지막** 관측치 인덱스다(창은
    end_indices[i] - window + 1 부터 end_indices[i] 까지, 양끝 포함).
    """

    window: int
    end_indices: list[int]
    values: list[float | None]

    def __post_init__(self) -> None:
        if len(self.end_indices) != len(self.values):
            raise ValueError("end_indices와 values의 길이가 다르다")

    @property
    def is_empty(self) -> bool:
        return len(self.values) == 0

    @property
    def defined_values(self) -> list[float]:
        """None을 뺀 값들. 평균·최대·최소를 낼 때 쓴다."""
        return [v for v in self.values if v is not None]

    def labels(self, dates: Sequence[date], fmt: str = "%y-%m") -> list[str]:
        """차트 x축 라벨 — 각 창의 마지막 날짜를 포맷팅한다."""
        return [dates[i].strftime(fmt) for i in self.end_indices]

    def to_plot_values(self, fill: float = 0.0) -> list[float]:
        """차트용 float 리스트. None을 fill로 바꾼다.

        기본값 0.0을 쓸 때는 주의 — "정의되지 않음"과 "0"이 구분되지 않는다.
        정의되지 않은 구간이 있으면 캡션에 그 사실을 함께 적어야 한다.
        """
        return [fill if v is None else v for v in self.values]


def _as_array(returns) -> np.ndarray:
    arr = np.asarray(list(returns), dtype=float)
    if arr.ndim != 1:
        raise ValueError("수익률은 1차원 시계열이어야 한다")
    return arr


def _validate_window(window: int, n: int) -> bool:
    """창 크기를 검증하고, 계산 가능한지(관측치가 충분한지) 알려준다."""
    if window < 2:
        raise ValueError(f"창 크기는 2 이상이어야 한다: {window}")
    # 관측치 부족은 에러가 아니다 — 백필 중에는 정상적인 상태이며, 호출부는
    # 빈 시리즈를 받아 "이력 부족"으로 표시하면 된다.
    return n >= window


def rolling_apply(
    returns, window: int, fn: Callable[[np.ndarray], float | None]
) -> RollingSeries:
    """수익률 시계열에 창을 굴리며 fn을 적용한다. 다른 롤링 함수의 기반."""
    r = _as_array(returns)
    if not _validate_window(window, len(r)):
        return RollingSeries(window=window, end_indices=[], values=[])

    end_indices = list(range(window - 1, len(r)))
    values = [fn(r[i - window + 1 : i + 1]) for i in end_indices]
    return RollingSeries(window=window, end_indices=end_indices, values=values)


def rolling_apply_pair(
    a, b, window: int, fn: Callable[[np.ndarray, np.ndarray], float | None]
) -> RollingSeries:
    """두 시계열(포트폴리오·벤치마크)에 창을 굴리며 fn을 적용한다."""
    x, y = _as_array(a), _as_array(b)
    if len(x) != len(y):
        raise ValueError(f"두 시계열의 길이가 다르다: {len(x)} vs {len(y)}")
    if not _validate_window(window, len(x)):
        return RollingSeries(window=window, end_indices=[], values=[])

    end_indices = list(range(window - 1, len(x)))
    values = [
        fn(x[i - window + 1 : i + 1], y[i - window + 1 : i + 1]) for i in end_indices
    ]
    return RollingSeries(window=window, end_indices=end_indices, values=values)


def rolling_volatility(
    returns, window: int, periods_per_year: int = TRADING_DAYS_PER_YEAR
) -> RollingSeries:
    """롤링 연환산 변동성."""
    return rolling_apply(returns, window, lambda w: annualized_volatility(w, periods_per_year))


def rolling_sharpe(
    returns,
    window: int,
    risk_free_rate: float = 0.0,
    periods_per_year: int = TRADING_DAYS_PER_YEAR,
) -> RollingSeries:
    """롤링 Sharpe. 창 내 변동성이 0이면 그 점은 None이다(0이 아니다)."""
    return rolling_apply(
        returns, window, lambda w: sharpe_ratio(w, risk_free_rate, periods_per_year)
    )


def rolling_return(returns, window: int) -> RollingSeries:
    """롤링 누적수익률 = ∏(1+r) − 1. 12개월 창이면 '최근 1년 수익률'이다."""
    return rolling_apply(returns, window, lambda w: float(np.prod(1.0 + w) - 1.0))


def rolling_max_drawdown(returns, window: int) -> RollingSeries:
    """롤링 최대낙폭(음수). 창 안에서만 고점을 잡으므로 전구간 MDD보다 얕다."""
    return rolling_apply(returns, window, lambda w: max_drawdown(w).max_drawdown)


def _correlation(x: np.ndarray, y: np.ndarray) -> float | None:
    sx, sy = np.std(x, ddof=1), np.std(y, ddof=1)
    if sx < _NEGLIGIBLE or sy < _NEGLIGIBLE:
        # 한쪽이 상수면 상관계수가 정의되지 않는다. numpy는 여기서 NaN과
        # RuntimeWarning을 내는데, 그 NaN이 차트까지 흘러가면 0으로 찍힌다.
        return None
    return float(np.corrcoef(x, y)[0, 1])


def rolling_correlation(a, b, window: int) -> RollingSeries:
    """두 시계열의 롤링 피어슨 상관계수.

    분산투자 효과가 국면에 따라 사라지는지(위기 때 상관계수가 1로 몰리는 현상)를
    보여주는 지표다 — 전구간 상관계수 하나로는 절대 드러나지 않는다.
    """
    return rolling_apply_pair(a, b, window, _correlation)


def _beta(x: np.ndarray, y: np.ndarray) -> float | None:
    var_y = float(np.var(y, ddof=1))
    if var_y < _NEGLIGIBLE:
        return None
    return float(np.cov(x, y, ddof=1)[0, 1] / var_y)


def rolling_beta(portfolio, benchmark, window: int) -> RollingSeries:
    """벤치마크 대비 롤링 베타. 시장 노출이 시간에 따라 어떻게 변했는지 본다."""
    return rolling_apply_pair(portfolio, benchmark, window, _beta)


def rolling_tracking_error(
    portfolio, benchmark, window: int, periods_per_year: int = TRADING_DAYS_PER_YEAR
) -> RollingSeries:
    """롤링 추적오차 = 초과수익(active return)의 연환산 표준편차."""
    return rolling_apply_pair(
        portfolio, benchmark, window, lambda p, b: annualized_volatility(p - b, periods_per_year)
    )


@dataclass(frozen=True)
class RollingSummary:
    """롤링 시리즈를 한 줄로 요약한다 — 리포트 캡션·표에 쓴다."""

    label: str
    window: int
    latest: float | None
    minimum: float | None
    maximum: float | None
    average: float | None
    undefined_count: int
    observations: int
    # 지면에 표시할 창 설명. "252기"는 독자에게 아무 의미가 없으므로
    # 호출부가 "12개월(252거래일)" 같은 문구를 준다.
    window_label: str = ""

    @property
    def window_text(self) -> str:
        return self.window_label or f"{self.window}기"

    def describe(self, fmt: str = "{:.2f}") -> str:
        if self.observations == 0:
            return f"{self.label}: 이력 부족({self.window_text} 창을 채우지 못함)"

        def f(v: float | None) -> str:
            return "—" if v is None else fmt.format(v)

        text = (
            f"{self.label}({self.window_text} 롤링): 최근 {f(self.latest)}, "
            f"범위 {f(self.minimum)}~{f(self.maximum)}, 평균 {f(self.average)}"
        )
        if self.undefined_count:
            text += f" (정의 불가 {self.undefined_count}개 제외)"
        return text


def summarize(series: RollingSeries, label: str, window_label: str = "") -> RollingSummary:
    """롤링 시리즈 요약. 값이 하나도 없으면 전부 None으로 둔다 — 0으로 채우지 않는다."""
    defined = series.defined_values
    return RollingSummary(
        label=label,
        window_label=window_label,
        window=series.window,
        latest=series.values[-1] if series.values else None,
        minimum=min(defined) if defined else None,
        maximum=max(defined) if defined else None,
        average=float(np.mean(defined)) if defined else None,
        undefined_count=len(series.values) - len(defined),
        observations=len(series.values),
    )
