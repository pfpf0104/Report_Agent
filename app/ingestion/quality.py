"""데이터 품질 게이트 — 적재된 데이터를 리포트가 그대로 믿지 않도록 검사한다.

`ingestion_run.status = 'success'`는 "API 호출이 성공했다"는 뜻일 뿐, 들어온
숫자가 맞다는 뜻이 아니다. 이 모듈은 그 간극을 메운다.

## 검사 항목

  1. 스테일  — 마지막으로 알 수 있게 된 데이터가 너무 오래됐는가
  2. 결측    — 영업일인데 데이터가 없는 구간이 있는가
  3. 이상치  — 일간 변동이 비상식적인가
  4. 단위    — 값이 해당 자산유형의 상식 범위를 벗어나는가

## 4번(단위)이 특히 중요한 이유

BOK ECOS는 국고채 금리를 퍼센트로 준다("2.659" = 2.659%). 그런데
`duration_controller.compute_carry_price_gate()`의 파라미터명은 `yield_3y_bp`,
`yield_1y_bp` — **베이시스포인트**를 기대한다. 현재는 `city_ai_stub`이 300.0(=300bp)을
공급해 이 불일치가 드러나지 않지만, Phase 0에서 실제 BOK 데이터를 컨트롤러에
연결하는 순간 2.659를 265.9 자리에 넣는 **100배 오류**가 발생한다.

그런 오류는 예외를 던지지 않는다 — 2.659는 그 자체로 그럴듯한 숫자라 조용히
틀린 목표 듀레이션을 만들어낸다. 그래서 자릿수 자체를 검사한다.

## 사용

    report = run_quality_gate(db, as_of=date.today())
    if not report.ok:
        for issue in report.errors:
            ...

이 모듈은 판단만 하고 차단하지 않는다. 무엇을 ERROR로 볼지, 리포트 생성을
막을지는 호출부가 정한다 — 대시보드 조회와 배포용 리포트의 기준이 다르기 때문이다.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

import numpy as np
from sqlalchemy.orm import Session

from app.db.models.dim_asset import AssetType, DimAsset
from app.db.models.fact_market_daily import FactMarketDaily
from app.db.point_in_time import visible_as_of

ERROR = "error"
WARNING = "warning"

# 자산유형×통화별 상식 범위. 단위 오류(100배·소수점 위치)를 잡는 것이 목적이므로
# 정밀한 밸류에이션 기준이 아니라 "자릿수가 맞는가" 수준으로 잡는다.
#
# 통화를 함께 키로 두는 이유: KRW 표시 자산(원화 액면가가 커서 좌수당 5~10만원대가
# 흔함 — 예: 통안채1년 ETF 122260이 103,960원)과 USD 표시 자산(XLE $59, SPY $747)을
# 같은 상한으로 묶으면 정상적인 KRW 가격이 상한을 넘어 오탐(false positive)이
# 난다(2026-08 실측: 122260 종가 101,230원이 옛 ETF 상한 100,000을 넘어 매일
# ERROR로 잡히던 것을 실제 알림에서 확인하고 수정).
#
# MACRO는 이 프로젝트에서 국고채 금리를 담으며, 단위 규약은 **베이시스포인트**다
# (duration_controller의 yield_*_bp 파라미터와 맞춘다). 금리는 통화별로 자릿수가
# 갈리지 않으므로 KRW 키 하나만 둔다(현재 국고채만 취급).
#
# 하한을 0이 아니라 10bp로 둔 이유: 잡으려는 것이 바로 "퍼센트 값(2.659)을 bp
# 자리에 넣는" 오류인데, 하한이 0이면 2.659가 범위 안에 들어가 검사를 통과해
# 버린다(테스트로 실제 확인함). 국고채 금리가 0.1%를 밑도는 상황은 현실적으로
# 없으므로, 10bp 미만은 실제 저금리보다 단위 오류일 가능성이 압도적이다.
# MACRO_INDEX(스프레드·지수, USD)는 원단위 그대로 저장한다(위 AssetType
# docstring 참고). 스프레드(T10Y2Y 등)는 금리곡선 역전 시 음수가 될 수 있어
# 하한을 음수로 열어둔다 — MACRO(bp, 항상 양수)와 반대다. 지수(달러인덱스)는
# 100 안팎이라 상한도 훨씬 낮다. 이 asset_type 안에 스프레드(-5~15)와 지수
# (50~200)가 섞여 있어 상식범위를 넓게 잡는다 — 목적이 정밀한 이상치 탐지가
# 아니라 "자릿수가 완전히 틀렸는가"(예: bp로 착각해 ×100 되어 있는가)이므로
# 이 정도 폭이면 충분하다.
PLAUSIBLE_RANGES: dict[tuple[str, str], tuple[float, float]] = {
    (AssetType.MACRO.value, "KRW"): (10.0, 2000.0),
    (AssetType.MACRO.value, "USD"): (-500.0, 2000.0),
    (AssetType.MACRO_INDEX.value, "USD"): (-10.0, 200.0),
    (AssetType.EQUITY.value, "KRW"): (100.0, 10_000_000.0),
    (AssetType.EQUITY.value, "USD"): (0.1, 100_000.0),
    (AssetType.ETF.value, "KRW"): (1.0, 1_000_000.0),
    (AssetType.ETF.value, "USD"): (0.1, 100_000.0),
}

# 위 매핑에 (asset_type, currency) 조합이 없을 때 쓰는 대체 범위 — 새 통화가
# 추가돼도 검사 자체가 조용히 스킵되지 않게 한다.
_FALLBACK_RANGE: dict[str, tuple[float, float]] = {
    AssetType.MACRO.value: (-500.0, 2000.0),
    AssetType.MACRO_INDEX.value: (-10.0, 200.0),
    AssetType.EQUITY.value: (0.1, 10_000_000.0),
    AssetType.ETF.value: (0.1, 1_000_000.0),
}

# 이 값보다 작으면 "퍼센트를 bp 자리에 넣었다"는 조치 힌트를 붙인다
# (bp로는 비상식적이지만 퍼센트로는 그럴듯한 구간).
_PERCENT_LOOKING_BP_THRESHOLD = 100.0

# 일간 변동 임계치(%). 넘으면 이상치로 본다. 실제로 발생 가능한 폭(2020년 3월 등)을
# 감안해 "불가능"이 아니라 "확인 필요" 수준으로 잡는다.
MAX_DAILY_MOVE_PCT: dict[str, float] = {
    AssetType.EQUITY.value: 35.0,
    AssetType.ETF.value: 25.0,
}

# MACRO/MACRO_INDEX는 상대변동%이 아니라 절대변동으로 이상치를 본다.
#
# MACRO_INDEX(스프레드·지수, T10Y2Y 등)는 0 근처를 오가는 %p 값이라, 절대값이
# 작을 때 상대변동%이 수백~수천%로 발산해 매일 오탐이 난다(2026-08 실측:
# US10Y2Y가 0.04→-0.05로 9bp만 움직였는데 상대변동은 225%로 잡혀 222건의
# 경고가 쏟아졌다 — 알림 시스템이 이걸 실제로 매일 전송하게 됐을 것이다).
#
# MACRO(금리, bp)도 같은 문제를 겪는다 — 저금리 구간(예: 2021년 US1MO
# 3~10bp)에서는 1bp 변화만으로 상대변동%이 30~50%로 튄다(같은 실측에서
# 65건 확인). 두 asset_type 모두 절대변동 임계치(저장 단위 그대로 — 금리는
# bp, 스프레드는 %p, 지수는 포인트)로 통일한다.
MAX_DAILY_MOVE_ABS: dict[str, float] = {
    AssetType.MACRO.value: 50.0,  # 금리(bp) — 하루 50bp 이상 변화면 확인 필요
    AssetType.MACRO_INDEX.value: 3.0,  # 스프레드(%p)·지수(포인트) 공통 — 하루 3 단위 이상 변화면 확인 필요
}

STALE_AFTER_DAYS = 7  # 주말·공휴일 연휴를 감안한 기본값

# 자산코드별 스테일 임계 예외. 연준 H.10(달러지수) 발표 자체가 실측으로 확인한
# 결과 며칠 지연된다(2026-08 실측: DTWEXBGS observation_end가 조회일 대비
# 9일 전 — FRED API 응답 메타데이터로 확인, 인제스천 실패가 아니라 발표
# 스케줄의 정상적인 특성). 기본 7일로는 매일 오탐이 나 15일로 넉넉히 둔다.
STALE_AFTER_DAYS_OVERRIDE: dict[str, int] = {
    "USDINDEX": 15,
}


@dataclass(frozen=True)
class QualityIssue:
    severity: str
    check: str
    asset_code: str
    detail: str

    def __str__(self) -> str:
        mark = "✖" if self.severity == ERROR else "▲"
        return f"{mark} [{self.check}] {self.asset_code}: {self.detail}"


@dataclass
class QualityReport:
    as_of: date
    issues: list[QualityIssue] = field(default_factory=list)

    @property
    def errors(self) -> list[QualityIssue]:
        return [i for i in self.issues if i.severity == ERROR]

    @property
    def warnings(self) -> list[QualityIssue]:
        return [i for i in self.issues if i.severity == WARNING]

    @property
    def ok(self) -> bool:
        return not self.errors

    def summary(self) -> str:
        if not self.issues:
            return f"{self.as_of} 기준 품질 검사 통과 — 이상 없음"
        return (
            f"{self.as_of} 기준 품질 검사: 오류 {len(self.errors)}건, "
            f"경고 {len(self.warnings)}건"
        )


def _rows_for(db: Session, asset: DimAsset, as_of: date) -> list[FactMarketDaily]:
    """as_of 시점에 알 수 있었던 시세만, 거래일 오름차순으로."""
    return (
        visible_as_of(db.query(FactMarketDaily), FactMarketDaily, as_of)
        .filter(FactMarketDaily.asset_id == asset.asset_id, FactMarketDaily.trade_date <= as_of)
        .order_by(FactMarketDaily.trade_date.asc())
        .all()
    )


def check_has_data(asset: DimAsset, rows: list[FactMarketDaily]) -> list[QualityIssue]:
    if rows:
        return []
    return [
        QualityIssue(
            ERROR, "has_data", asset.code,
            "해당 시점까지 조회 가능한 데이터가 한 건도 없다 — 인제스천이 실행되지 않았거나 knowledge_date가 미래로 잡혀 있다",
        )
    ]


def check_staleness(
    asset: DimAsset, rows: list[FactMarketDaily], as_of: date, stale_after_days: int = STALE_AFTER_DAYS
) -> list[QualityIssue]:
    if not rows:
        return []
    latest = max(r.trade_date for r in rows)
    gap = (as_of - latest).days
    if gap > stale_after_days:
        return [
            QualityIssue(
                ERROR, "staleness", asset.code,
                f"마지막 데이터가 {latest} — {gap}일 경과(임계 {stale_after_days}일). 스케줄러 또는 API 실패 의심",
            )
        ]
    return []


def check_value_range(asset: DimAsset, rows: list[FactMarketDaily]) -> list[QualityIssue]:
    """자산유형×통화별 상식 범위를 벗어나는 값 — 단위 오류를 잡는 핵심 검사."""
    bounds = PLAUSIBLE_RANGES.get((asset.asset_type, asset.currency)) or _FALLBACK_RANGE.get(
        asset.asset_type
    )
    if bounds is None or not rows:
        return []

    low, high = bounds
    offenders = [r for r in rows if r.close is not None and not (low <= float(r.close) <= high)]
    if not offenders:
        return []

    sample = offenders[0]
    hint = ""
    if asset.asset_type == AssetType.MACRO.value and float(sample.close) < _PERCENT_LOOKING_BP_THRESHOLD:
        # 2.659 같은 값 — 퍼센트를 bp 자리에 넣은 전형적 형태
        hint = " (퍼센트를 bp 자리에 넣지 않았는지 확인 — ×100 필요할 수 있음)"

    return [
        QualityIssue(
            ERROR, "value_range", asset.code,
            f"{len(offenders)}건이 상식 범위 [{low:,.0f}, {high:,.0f}] 밖. "
            f"예: {sample.trade_date} close={float(sample.close):,.4f}{hint}",
        )
    ]


def check_outliers(asset: DimAsset, rows: list[FactMarketDaily]) -> list[QualityIssue]:
    closes = [(r.trade_date, float(r.close)) for r in rows if r.close is not None]
    if len(closes) < 2:
        return []

    issues = []
    if asset.asset_type in MAX_DAILY_MOVE_ABS:
        # 스프레드·지수(MACRO_INDEX): 0 근처를 오가는 값이라 상대변동%은
        # 발산한다 — 절대변동으로 본다.
        threshold = MAX_DAILY_MOVE_ABS[asset.asset_type]
        for (_, prev), (d, cur) in zip(closes, closes[1:]):
            move = abs(cur - prev)
            if move > threshold:
                issues.append(
                    QualityIssue(
                        WARNING, "outlier", asset.code,
                        f"{d} 일간 변동 {move:+.2f} (임계 {threshold:.1f}) — {prev:,.4f} → {cur:,.4f}",
                    )
                )
    else:
        threshold = MAX_DAILY_MOVE_PCT.get(asset.asset_type, 50.0)
        for (_, prev), (d, cur) in zip(closes, closes[1:]):
            if prev == 0:
                continue
            move = abs(cur / prev - 1) * 100
            if move > threshold:
                issues.append(
                    QualityIssue(
                        WARNING, "outlier", asset.code,
                        f"{d} 일간 변동 {move:.1f}% (임계 {threshold:.0f}%) — {prev:,.4f} → {cur:,.4f}",
                    )
                )
    return issues


def check_missing_business_days(
    asset: DimAsset, rows: list[FactMarketDaily], max_gap_days: int = 5
) -> list[QualityIssue]:
    """영업일 기준으로 연속 결측 구간을 찾는다(공휴일은 반영하지 않는 근사)."""
    dates = sorted({r.trade_date for r in rows})
    if len(dates) < 2:
        return []

    issues = []
    for prev, cur in zip(dates, dates[1:]):
        gap = int(np.busday_count(prev, cur)) - 1
        if gap > max_gap_days:
            issues.append(
                QualityIssue(
                    WARNING, "missing_days", asset.code,
                    f"{prev} ~ {cur} 사이 영업일 {gap}일 결측(임계 {max_gap_days}일)",
                )
            )
    return issues


def run_quality_gate(
    db: Session, as_of: date, asset_codes: list[str] | None = None
) -> QualityReport:
    """지정 자산(생략 시 dim_asset 전체)에 대해 모든 검사를 돌린다."""
    report = QualityReport(as_of=as_of)

    query = db.query(DimAsset)
    if asset_codes is not None:
        query = query.filter(DimAsset.code.in_(asset_codes))
    assets = query.order_by(DimAsset.code).all()

    if not assets:
        report.issues.append(
            QualityIssue(
                ERROR, "has_assets", "-",
                "dim_asset이 비어 있다 — 인제스천이 한 번도 실행되지 않았다",
            )
        )
        return report

    for asset in assets:
        rows = _rows_for(db, asset, as_of)

        missing = check_has_data(asset, rows)
        report.issues.extend(missing)
        if missing:
            continue  # 데이터가 없으면 나머지 검사는 의미 없다

        stale_threshold = STALE_AFTER_DAYS_OVERRIDE.get(asset.code, STALE_AFTER_DAYS)
        report.issues.extend(check_staleness(asset, rows, as_of, stale_threshold))
        report.issues.extend(check_value_range(asset, rows))
        report.issues.extend(check_outliers(asset, rows))
        report.issues.extend(check_missing_business_days(asset, rows))

    return report
