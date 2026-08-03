"""역사적 레짐 패턴 매칭 — MASTER_PLAN Phase 3-4 후반부.

narrative.py(관찰 서술)보다 한 단계 더 나아간 주장을 한다: "현재와 같은
사분면이었던 과거 시점이 언제였는가"를 찾는다. 이것도 인과관계를 주장하지
않지만("과거에 이런 국면이 있었다"), 독자가 "그러니까 이번에도 비슷하게
움직이겠다"로 오독할 위험이 narrative.py보다 크다 — 그래서 별도 모듈로
분리했고(모듈 docstring에서 예고한 대로), 결과에는 항상 "과거가 미래를
예측하지 않는다"는 명시적 경고가 붙는다.

## 무엇을 매칭하는가

classify_regime()과 동일한 판정 지표(산업생산·CPI YoY 추세)로 과거 매월의
사분면을 재구성하고, 현재와 같은 사분면이었던 시점을 찾는다. 재구성이지
새로운 예측 모델이 아니다 — classify_regime()이 특정 as_of 하나에 대해
하는 계산을, 여러 as_of에 대해 반복할 뿐이다.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from sqlalchemy.orm import Session

from app.computation.regime.classifier import (
    GROWTH_CODE,
    INFLATION_CODE,
    MIN_MONTHS_FOR_TREND,
    QUADRANT_LABELS,
    compute_yoy_series,
    load_monthly_series,
)


@dataclass(frozen=True)
class HistoricalQuadrant:
    month: date
    quadrant: str


@dataclass(frozen=True)
class AnalogContext:
    available: bool
    current_quadrant: str | None
    analog_months: list[HistoricalQuadrant]
    total_months_analyzed: int
    data_status: str


def _pending(reason: str) -> AnalogContext:
    return AnalogContext(
        available=False, current_quadrant=None, analog_months=[], total_months_analyzed=0,
        data_status=reason,
    )


def build_quadrant_history(db: Session, as_of: date) -> list[HistoricalQuadrant]:
    """as_of 시점에 알 수 있었던 이력 전체에서, 매월 사분면을 재구성한다.

    classify_regime()과 같은 데이터·같은 판정 로직을 쓰지만 한 시점이 아니라
    가능한 모든 월에 대해 반복한다. 결과는 오름차순(과거→최근)이다.
    """
    growth_obs = load_monthly_series(db, GROWTH_CODE, as_of)
    inflation_obs = load_monthly_series(db, INFLATION_CODE, as_of)
    if len(growth_obs) < MIN_MONTHS_FOR_TREND or len(inflation_obs) < MIN_MONTHS_FOR_TREND:
        return []

    growth_yoy = compute_yoy_series(growth_obs)
    inflation_yoy = compute_yoy_series(inflation_obs)

    # 두 시리즈의 관측월이 다를 수 있으니(발표일 차이) 공통 월만 쓴다.
    growth_by_month = dict(growth_yoy)
    inflation_by_month = dict(inflation_yoy)
    common_months = sorted(set(growth_by_month) & set(inflation_by_month))

    history = []
    prev_growth = None
    prev_inflation = None
    for month in common_months:
        g = growth_by_month[month]
        i = inflation_by_month[month]
        if prev_growth is not None and prev_inflation is not None:
            growth_accel = g > prev_growth
            inflation_accel = i > prev_inflation
            history.append(HistoricalQuadrant(month, QUADRANT_LABELS[(growth_accel, inflation_accel)]))
        prev_growth, prev_inflation = g, i
    return history


def find_historical_analogs(db: Session, as_of: date, *, max_results: int = 5) -> AnalogContext:
    """현재(as_of 기준 최신월)와 같은 사분면이었던 과거 월을 최대 max_results개 찾는다.

    현재 자신은 결과에서 제외한다(같은 달과 비교하는 건 의미가 없다).
    """
    history = build_quadrant_history(db, as_of)
    if not history:
        return _pending(f"레짐 이력 재구성 불가 — 최소 {MIN_MONTHS_FOR_TREND}개월 필요")

    current = history[-1]
    past = history[:-1]
    matches = [h for h in past if h.quadrant == current.quadrant]
    # 최근 것부터 보여준다(더 최근 사례가 통상 더 관련성 있게 읽힌다).
    matches_recent_first = list(reversed(matches))[:max_results]

    return AnalogContext(
        available=True,
        current_quadrant=current.quadrant,
        analog_months=matches_recent_first,
        total_months_analyzed=len(history),
        data_status=f"{history[0].month.strftime('%Y-%m')} ~ {history[-1].month.strftime('%Y-%m')} ({len(history)}개월) 재구성",
    )


def build_analog_report_context(db: Session, as_of: date) -> dict:
    """report_context 계열과 동일한 dict-반환 컨벤션."""
    ctx = find_historical_analogs(db, as_of)
    if not ctx.available:
        return {
            "analog_available": False,
            "analog_data_status": ctx.data_status,
        }

    return {
        "analog_available": True,
        "analog_current_quadrant": ctx.current_quadrant,
        "analog_data_status": ctx.data_status,
        "analog_months_rows": [[h.month.strftime("%Y-%m"), h.quadrant] for h in ctx.analog_months],
        "analog_has_matches": len(ctx.analog_months) > 0,
        "analog_disclosure": (
            f"위 시점들은 현재와 같은 사분면('{ctx.current_quadrant}')으로 재구성된 "
            "과거 관측일 뿐이다. 같은 사분면이었다고 해서 그 시점 이후의 자산 성과가 "
            "재현된다는 뜻이 아니며, 이 리포트는 그 시점 이후 실제로 무슨 일이 "
            "있었는지 조회하거나 제시하지 않는다. 과거 레짐 재현이 향후 성과를 "
            "예측하지 않는다."
        ),
    }
