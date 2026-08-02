"""성장×인플레이션 4분면 레짐 분류기 (MASTER_PLAN Phase 3-1, All Weather 프레임).

## 판정 지표 vs 보조 확인 지표

4분면 판정은 딱 2개 지표(성장=산업생산 USINDPRO, 인플레=CPI USCPI)의 YoY
변화율 추세 방향에만 의존한다. GDP·PCE·고용(USGDP/USPCE/USPAYEMS)도 같이
인제스천하지만 판정에는 관여하지 않는다 — 여러 지표를 동시에 판정에 쓰면
서로 다른 방향을 가리킬 때(예: 산업생산은 가속인데 GDP는 감속) 판정 자체가
모호해진다. 판정 로직은 검증 가능하게 단순히 유지하고, 나머지 지표는
페이지에 참고용으로만 병기한다(app/ingestion/jobs/ingest_macro_indicators.py
의 REGIME_DECISION_SERIES/REGIME_REFERENCE_SERIES 분리와 대응).

## "추세 방향"의 정의 — 시장 예상치가 없어서

정통 All Weather 프레임은 각 축을 "시장 예상 대비 실제치의 방향"으로 나눈다
(예상보다 성장이 좋았는가/나빴는가). 이 프로젝트는 컨센서스 예상치 데이터를
갖고 있지 않다. 대신 훨씬 약한 주장을 쓴다: **YoY 변화율 자체가 직전 관측
대비 가속(더 커짐)했는가 감속(더 작아짐)했는가**. 이건 시장 예상과 무관하게
데이터에서 직접 계산 가능한 사실이다.

성장 가속(YoY 산업생산 증가율이 커짐) × 인플레 가속(YoY CPI 상승률이 커짐)
→ "과열"(overheating), 성장 가속 × 인플레 감속 → "골디락스"(goldilocks),
성장 감속 × 인플레 가속 → "스태그플레이션"(stagflation), 성장 감속 × 인플레
감속 → "둔화"(slowdown).

## 최소 이력 요건

YoY 계산에 13개월(전년동월+당월), 추세 판정에 그 YoY 값 2개(이번달·전달)가
필요해 최소 14개월 이력이 있어야 한다. 부족하면 숫자를 만들어내지 않고
보류 컨텍스트를 반환한다 — 성과 페이지·크로스에셋 페이지와 동일한 원칙.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from sqlalchemy.orm import Session

from app.db.models.dim_asset import DimAsset
from app.db.models.fact_market_daily import FactMarketDaily
from app.db.point_in_time import visible_as_of

GROWTH_CODE = "USINDPRO"
INFLATION_CODE = "USCPI"

MIN_MONTHS_FOR_YOY = 13  # 전년동월 비교에 필요한 최소 개월수(당월 포함)
MIN_MONTHS_FOR_TREND = MIN_MONTHS_FOR_YOY + 1  # 추세 판정에는 YoY 값이 2개 필요

QUADRANT_LABELS = {
    (True, True): "과열 (Overheating)",
    (True, False): "골디락스 (Goldilocks)",
    (False, True): "스태그플레이션 (Stagflation)",
    (False, False): "둔화 (Slowdown)",
}


@dataclass(frozen=True)
class SeriesObservation:
    trade_date: date
    value: float


@dataclass(frozen=True)
class RegimeContext:
    available: bool
    quadrant: str | None
    growth_accelerating: bool | None
    inflation_accelerating: bool | None
    growth_yoy_pct: float | None
    growth_yoy_pct_prior: float | None
    inflation_yoy_pct: float | None
    inflation_yoy_pct_prior: float | None
    as_of_month: date | None
    data_status: str


def load_monthly_series(db: Session, code: str, as_of: date) -> list[SeriesObservation]:
    """as_of 시점에 알 수 있었던 관측치만, 관측월 오름차순으로."""
    asset = db.query(DimAsset).filter_by(code=code).first()
    if asset is None:
        return []
    rows = (
        visible_as_of(db.query(FactMarketDaily), FactMarketDaily, as_of)
        .filter(
            FactMarketDaily.asset_id == asset.asset_id,
            FactMarketDaily.trade_date <= as_of,
            FactMarketDaily.close.isnot(None),
        )
        .order_by(FactMarketDaily.trade_date.asc())
        .all()
    )
    return [SeriesObservation(r.trade_date, float(r.close)) for r in rows]


def compute_yoy_series(
    observations: list[SeriesObservation], periods_per_year: int = 12
) -> list[tuple[date, float]]:
    """관측치에서 YoY 변화율(%) 시계열을 만든다.

    periods_per_year는 그 시리즈의 발표 주기(월간=12, 분기=4)다. 관측치가
    등간격이라는 전제(FRED 월간/분기 시리즈의 date 필드는 항상 그 달/분기
    1일)로, 인덱스가 periods_per_year 떨어진 쌍을 전년동기로 본다 — 실제
    달력의 "1년 전"을 재계산하지 않고 관측 순서로 맞춘다(중간에 결측이
    없다는 가정 하에 동일한 결과, 있으면 이 근사가 어긋날 수 있음 — FRED
    표준 시리즈는 결측이 없어 실무상 문제 없다).

    호출부가 발표 주기를 명시적으로 넘기지 않으면 월간(12)을 가정한다 —
    분기 시리즈(GDP 등)에 이 기본값을 그대로 쓰면 인덱스 12칸 전이 실제로는
    3년 전이 되어 YoY가 3년 누적 변화율로 계산되는 조용한 오류가 난다
    (2026-08 실측: USGDP에 periods_per_year=12를 잘못 적용해 YoY가
    +18.95%로 나온 것을 발견 — 정상 분기 GDP YoY는 통상 한 자릿수).
    """
    result = []
    for i in range(periods_per_year, len(observations)):
        base = observations[i - periods_per_year].value
        current = observations[i].value
        if base == 0:
            continue
        yoy_pct = (current / base - 1) * 100
        result.append((observations[i].trade_date, yoy_pct))
    return result


def _pending(reason: str) -> RegimeContext:
    return RegimeContext(
        available=False, quadrant=None, growth_accelerating=None, inflation_accelerating=None,
        growth_yoy_pct=None, growth_yoy_pct_prior=None, inflation_yoy_pct=None,
        inflation_yoy_pct_prior=None, as_of_month=None, data_status=reason,
    )


def classify_regime(db: Session, as_of: date) -> RegimeContext:
    """as_of 시점까지 알 수 있었던 산업생산·CPI로 성장×인플레 4분면을 판정한다."""
    growth_obs = load_monthly_series(db, GROWTH_CODE, as_of)
    inflation_obs = load_monthly_series(db, INFLATION_CODE, as_of)

    if len(growth_obs) < MIN_MONTHS_FOR_TREND or len(inflation_obs) < MIN_MONTHS_FOR_TREND:
        n = min(len(growth_obs), len(inflation_obs))
        return _pending(f"이력 {n}개월 확보 — 최소 {MIN_MONTHS_FOR_TREND}개월 필요")

    growth_yoy = compute_yoy_series(growth_obs)
    inflation_yoy = compute_yoy_series(inflation_obs)

    if len(growth_yoy) < 2 or len(inflation_yoy) < 2:
        return _pending("YoY 계산 가능 구간 부족 — 추세 판정에는 YoY 값 2개 필요")

    growth_latest_date, growth_latest = growth_yoy[-1]
    _, growth_prior = growth_yoy[-2]
    inflation_latest_date, inflation_latest = inflation_yoy[-1]
    _, inflation_prior = inflation_yoy[-2]

    growth_accelerating = growth_latest > growth_prior
    inflation_accelerating = inflation_latest > inflation_prior

    quadrant = QUADRANT_LABELS[(growth_accelerating, inflation_accelerating)]

    # 두 지표의 최신 관측월이 다를 수 있다(산업생산·CPI 발표일이 다름) —
    # 더 이른 쪽을 "as_of_month"로 표기해 어느 시점 기준 판정인지 명시한다.
    as_of_month = min(growth_latest_date, inflation_latest_date)

    return RegimeContext(
        available=True,
        quadrant=quadrant,
        growth_accelerating=growth_accelerating,
        inflation_accelerating=inflation_accelerating,
        growth_yoy_pct=growth_latest,
        growth_yoy_pct_prior=growth_prior,
        inflation_yoy_pct=inflation_latest,
        inflation_yoy_pct_prior=inflation_prior,
        as_of_month=as_of_month,
        data_status=f"산업생산 {growth_latest_date.isoformat()} · CPI {inflation_latest_date.isoformat()} 기준",
    )
