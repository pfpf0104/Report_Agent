"""Brinson 성과 귀속 — 초과수익이 어디서 나왔는지 분해한다.

Brinson-Hood-Beebower(1986) 모델. 벤치마크 대비 초과수익을 세 갈래로 나눈다:

  배분효과(allocation)  = (w_p − w_b) × (R_b,i − R_b)
      "좋은 섹터를 더 담았는가" — 종목선택과 무관하게, 섹터 비중 결정만의 기여

  선택효과(selection)   = w_b × (R_p,i − R_b,i)
      "그 섹터 안에서 잘 골랐는가" — 비중 결정과 무관하게, 종목선택만의 기여

  상호작용(interaction) = (w_p − w_b) × (R_p,i − R_b,i)
      두 결정이 겹쳐 발생한 부분. 잘 고른 섹터를 더 담았을 때 커진다.

세 효과의 합은 정확히 총 초과수익(R_p − R_b)과 일치한다 — 이 항등식이 성립하지
않으면 계산이 틀린 것이므로, 테스트에서 이를 검증한다.

## 이 프로젝트에서의 의미

CallRank는 지금 섹터 랭킹만 내놓고, 그 랭킹이 실제로 초과수익에 기여했는지는
말하지 않는다. "1위로 지목한 섹터가 실제로 올랐는가"(배분효과)와 "그 섹터를
담았기 때문에 번 것인가"를 구분해야 신호의 가치를 판단할 수 있다.

## 단위 규약

비중·수익률 전부 **소수**(0.25 = 25%). 비중 합은 1.0이어야 한다.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

# 비중 합이 1.0에서 이만큼 벗어나면 입력 오류로 본다(반올림 오차는 허용).
_WEIGHT_SUM_TOLERANCE = 1e-6


@dataclass(frozen=True)
class SegmentAttribution:
    """구간(섹터 등) 단위 귀속 결과."""

    name: str
    portfolio_weight: float
    benchmark_weight: float
    portfolio_return: float
    benchmark_return: float
    allocation: float
    selection: float
    interaction: float

    @property
    def total(self) -> float:
        return self.allocation + self.selection + self.interaction

    @property
    def active_weight(self) -> float:
        return self.portfolio_weight - self.benchmark_weight


@dataclass(frozen=True)
class AttributionResult:
    segments: list[SegmentAttribution]
    portfolio_return: float
    benchmark_return: float

    @property
    def excess_return(self) -> float:
        return self.portfolio_return - self.benchmark_return

    @property
    def total_allocation(self) -> float:
        return sum(s.allocation for s in self.segments)

    @property
    def total_selection(self) -> float:
        return sum(s.selection for s in self.segments)

    @property
    def total_interaction(self) -> float:
        return sum(s.interaction for s in self.segments)


def _check_weights(weights: np.ndarray, label: str) -> None:
    if np.any(~np.isfinite(weights)):
        raise ValueError(f"{label} 비중에 NaN/무한대가 있다")
    total = float(weights.sum())
    if abs(total - 1.0) > _WEIGHT_SUM_TOLERANCE:
        raise ValueError(f"{label} 비중 합이 1.0이 아니다: {total:.6f}")


def brinson_attribution(
    segment_names: list[str],
    portfolio_weights,
    benchmark_weights,
    portfolio_returns,
    benchmark_returns,
) -> AttributionResult:
    """BHB 3요소 귀속.

    모든 인자는 같은 길이여야 하며, 인덱스가 같은 구간을 가리켜야 한다.
    """
    pw = np.asarray(list(portfolio_weights), dtype=float)
    bw = np.asarray(list(benchmark_weights), dtype=float)
    pr = np.asarray(list(portfolio_returns), dtype=float)
    br = np.asarray(list(benchmark_returns), dtype=float)

    lengths = {len(segment_names), len(pw), len(bw), len(pr), len(br)}
    if len(lengths) != 1:
        raise ValueError(f"입력 길이가 서로 다르다: {lengths}")
    if not segment_names:
        raise ValueError("구간이 비어 있다")

    _check_weights(pw, "포트폴리오")
    _check_weights(bw, "벤치마크")

    total_portfolio = float(pw @ pr)
    total_benchmark = float(bw @ br)

    active_weight = pw - bw
    relative_benchmark = br - total_benchmark
    active_return = pr - br

    allocation = active_weight * relative_benchmark
    selection = bw * active_return
    interaction = active_weight * active_return

    segments = [
        SegmentAttribution(
            name=name,
            portfolio_weight=float(pw[i]),
            benchmark_weight=float(bw[i]),
            portfolio_return=float(pr[i]),
            benchmark_return=float(br[i]),
            allocation=float(allocation[i]),
            selection=float(selection[i]),
            interaction=float(interaction[i]),
        )
        for i, name in enumerate(segment_names)
    ]

    return AttributionResult(
        segments=segments,
        portfolio_return=total_portfolio,
        benchmark_return=total_benchmark,
    )


def format_attribution_rows(result: AttributionResult) -> list[list[str]]:
    """리포트 템플릿용 문자열 행. 마지막에 합계 행을 붙인다."""
    def pct(v: float) -> str:
        return f"{v * 100:+.2f}%"

    rows = [
        [
            s.name,
            f"{s.portfolio_weight * 100:.1f}%",
            f"{s.benchmark_weight * 100:.1f}%",
            pct(s.active_weight),
            pct(s.allocation),
            pct(s.selection),
            pct(s.interaction),
            pct(s.total),
        ]
        for s in result.segments
    ]
    rows.append([
        "합계",
        "100.0%",
        "100.0%",
        "—",
        pct(result.total_allocation),
        pct(result.total_selection),
        pct(result.total_interaction),
        pct(result.excess_return),
    ])
    return rows
