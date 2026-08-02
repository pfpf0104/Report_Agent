"""레짐 4분면 판정에는 관여하지 않는 보조 확인 지표(GDP·PCE·고용) — 참고용 병기.

classifier.py가 판정을 산업생산·CPI 2개로만 제한하는 이유(classifier.py
docstring 참고)와 대칭으로, 여기서는 그 판정을 검증/보완할 수 있는 나머지
지표를 계산 없이 최신값+YoY만 보여준다 — 이 값들이 4분면 판정과 어긋나 보여도
그 자체가 오류는 아니다(서로 다른 성격의 지표라 자연스럽게 갈릴 수 있다).
"""
from __future__ import annotations

from datetime import date

from sqlalchemy.orm import Session

from app.computation.regime.classifier import (
    SeriesObservation,
    compute_yoy_series,
    load_monthly_series,
)
from app.ingestion.jobs.ingest_macro_indicators import REGIME_REFERENCE_SERIES

_LABELS = {
    "USGDP": "실질GDP(분기)",
    "USPCE": "PCE 물가지수(월간)",
    "USPAYEMS": "비농업고용(월간, 천명)",
}

# 발표 주기 — compute_yoy_series에 그대로 넘긴다. USGDP만 분기(4)라 나머지와
# 다르다(분기 시리즈에 월간 기본값 12를 쓰면 YoY가 3년 누적 변화율로 계산되는
# 조용한 오류가 난다 — classifier.py의 compute_yoy_series docstring 참고).
_PERIODS_PER_YEAR = {"USGDP": 4}
_DEFAULT_PERIODS_PER_YEAR = 12


def build_reference_indicator_rows(db: Session, as_of: date) -> list[list[str]]:
    """[지표명, 최신 관측월, 최신값, YoY%] 행 목록. 이력 부족 지표는 건너뛴다
    (숫자를 만들어내지 않는다 — 판정 지표와 동일 원칙이나, 보조 지표라
    개별적으로 이력이 부족해도 전체 페이지를 보류시키지 않는다)."""
    rows: list[list[str]] = []
    for code in REGIME_REFERENCE_SERIES:
        periods_per_year = _PERIODS_PER_YEAR.get(code, _DEFAULT_PERIODS_PER_YEAR)
        observations = load_monthly_series(db, code, as_of)
        if len(observations) < periods_per_year + 1:
            continue
        yoy = compute_yoy_series(observations, periods_per_year=periods_per_year)
        if not yoy:
            continue
        latest_date, latest_yoy = yoy[-1]
        latest_value = _latest_value_as_of(observations, latest_date)
        rows.append([
            _LABELS.get(code, code),
            latest_date.strftime("%Y-%m"),
            f"{latest_value:,.1f}",
            f"{latest_yoy:+.2f}%",
        ])
    return rows


def _latest_value_as_of(observations: list[SeriesObservation], target_date: date) -> float:
    for obs in observations:
        if obs.trade_date == target_date:
            return obs.value
    return observations[-1].value
