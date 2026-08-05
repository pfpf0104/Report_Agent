"""City AI의 예측 부분(predicted_change_bp)을 실제 PCA-Ridge 모델로 재현한다.

MetroGuard-KR 보고서 7페이지 방법론: 60개월 워크포워드로 매월 처음부터
재적합하고, 표준화 → PCA-8 압축 → penalty λ=10 고정 Ridge로 향후 63거래일
한국 3년 금리(KTB3Y) 변화를 예측한다.

## 이 모듈이 재현하는 것과 재현하지 않는 것

원본은 49개 입력(한국 금리곡선 19개·글로벌 금리 21개·미국 전국 주택 3개·
미국 도시 주택 6개)을 쓴다. 이 프로젝트는 그중 실측 소싱이 가능한 세 그룹을
쓴다 — 한국 금리(KTB1Y·KTB3Y, 2개), 미국 금리곡선·매크로 지표
(ingest_global_rates.py의 15개, USHYSPREAD 제외 — 아래 _EXCLUDED_FROM_TRAINING
참고), 미국 주택가격 지표(ingest_housing_indicators.py의 9개, 전국 3+도시 6,
원본과 시리즈가 정확히 일치하지는 않음 — 그 모듈 docstring 참고). 총 26개
입력으로, 원본의 49개에는 못 미치지만 "PCA-8 압축 → Ridge로 미래 금리변화를
예측한다"는 방법론 자체는 동일한 알고리즘으로 재현한다.

## 월간 지표를 일별 시계열에 맞추는 방법 — forward-fill

주택 지표는 월간·분기 발표라 한 달에 관측치가 1개뿐이다. 다른 일별 금리
시리즈와 "정확히 같은 날짜"로 교집합을 구하면 공통 거래일이 사실상 0에
가까워진다(월간 관측일과 거래일이 우연히 일치하는 날만 남기 때문). 대신
`_forward_fill_monthly()`가 각 월간 관측치를 "다음 관측치가 나올 때까지"
그대로 들고 있는 일별 시리즈로 바꾼다 — look-ahead가 아니다. 특정 거래일에
쓰는 값은 항상 그 날짜에 이미 공표된(knowledge_date ≤ 그 날짜) 가장 최근
관측치이므로, 실제로 그 시점에 알 수 있었던 값만 쓴다.

## 학습창을 60개월이 아닌 36개월로 둔 이유

원본 방법론의 60개월은 미국 국채가 수십 년치 이력을 갖는다는 전제다. 이
프로젝트는 KTB1Y/KTB3Y·미국 금리곡선 모두 2021년 이후 5년치만 인제스천했고
(Phase 0 백필 범위), 16개 지표 전체의 공통 거래일 교집합은 실측 1175거래일
(약 4.7년)이다. 60개월(1260거래일)+63거래일 라벨 성숙 구간을 요구하면
지금 데이터로는 영원히 학습이 불가능하다(2026-08 실측 확인). 36개월(약
750거래일)로 낮춰 지금 데이터로 실제 학습이 되는지 확인했다 — 데이터가
누적되면 TRAINING_WINDOW_MONTHS를 60으로 다시 올릴 수 있다(코드는 그대로,
상수만 조정).

## 왜 CallRank의 "고정 헤지"와 다른가

`ridge_sector_rank.py`의 CallRank 모델은 pre-2021 데이터로 한 번만 학습하고
이후 계수를 절대 바꾸지 않는다("고정 헤지"). MetroGuard는 반대다 — 매월
학습창(직전 60개월)으로 처음부터 다시 학습한다("워크포워드 재적합"). 그래서
`fit_frozen_hedge`를 재사용하지 않고 이 모듈에 별도 `fit_walk_forward_ridge`를
둔다 — 재사용 가능한 부분(표준화→PCA→Ridge 파이프라인 자체)은 같지만, 계수를
고정하느냐 매번 새로 맞추느냐는 정반대 설계 결정이다.

## Point-in-time / 라벨 성숙(label maturity)

t 시점에 학습할 때 라벨(y)은 "t+63거래일 시점의 KTB3Y − t 시점의 KTB3Y"다.
이 라벨은 t+63거래일이 지나야 실제로 관측되므로, 학습 데이터의 마지막
63거래일은 라벨이 아직 없다(미래가 아직 오지 않았다) — 그 구간은 학습에서
제외한다. 이건 look-ahead 방지가 아니라 "라벨이 존재하지 않는 데이터로
학습할 수 없다"는 산술적 제약이다.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import numpy as np
from sklearn.decomposition import PCA
from sklearn.linear_model import Ridge
from sklearn.preprocessing import StandardScaler
from sqlalchemy.orm import Session

from app.db.models.dim_asset import DimAsset
from app.db.models.fact_market_daily import FactMarketDaily
from app.db.point_in_time import visible_as_of

# 학습 입력 자산 코드. 한국 금리 2개 + ingest_global_rates.py의 16개.
KOREAN_RATE_CODES = ["KTB1Y", "KTB3Y"]

TARGET_CODE = "KTB3Y"
PREDICTION_HORIZON_TRADING_DAYS = 63  # 첨부 보고서와 동일(약 3개월)
PCA_COMPONENTS = 8
RIDGE_ALPHA = 10.0  # 첨부 보고서의 penalty λ=10 고정값
TRAINING_WINDOW_MONTHS = 36  # 원본은 60이지만 현재 보유 이력에 맞춰 축소(위 docstring 참고)
TRAINING_WINDOW_TRADING_DAYS = TRAINING_WINDOW_MONTHS * 21  # 월 평균 21거래일 근사

# 워크포워드 학습에 필요한 최소 관측치. 60개월 학습창 + 63거래일 라벨 성숙
# 구간이 최소한 있어야 학습 자체가 가능하다.
MIN_OBSERVATIONS_FOR_TRAINING = TRAINING_WINDOW_TRADING_DAYS + PREDICTION_HORIZON_TRADING_DAYS


# ingest_global_rates.py는 인제스천하지만 이 모델의 학습 입력에서는 제외하는
# 코드. USHYSPREAD(FRED BAMLH0A0HYM2)는 2026-08 실측 확인 결과 FRED 자체가
# "2026년 4월부터 3년치만 제공"한다고 공지한 시리즈라(ICE Data 라이선스 정책
# 변경, series 메타데이터 notes에 명시) 구조적으로 5년 이력을 채울 수 없다.
# 이 자산 하나 때문에 16개 지표 전체의 공통 거래일 교집합이 701개로 줄어
# 60개월(1260거래일) 학습창을 채우지 못하는 것을 실측으로 확인했다. 나머지
# 15개 지표는 전부 5년 이력(1175개 이상 교집합)이 있어 이 자산만 제외하면
# 학습창을 정상적으로 채울 수 있다. 인제스천은 계속한다 — 언젠가 다른 용도
# (예: 신용 리스크 참고 지표)로 유용할 수 있고, 학습에서만 빼는 게 맞다.
_EXCLUDED_FROM_TRAINING = {"USHYSPREAD"}


def _all_input_codes() -> list[str]:
    from app.ingestion.jobs.ingest_global_rates import ALL_SERIES as GLOBAL_RATE_ALL_SERIES
    from app.ingestion.jobs.ingest_housing_indicators import ALL_SERIES as HOUSING_ALL_SERIES

    return (
        KOREAN_RATE_CODES
        + [c for c in GLOBAL_RATE_ALL_SERIES.keys() if c not in _EXCLUDED_FROM_TRAINING]
        + list(HOUSING_ALL_SERIES.keys())
    )


def _monthly_input_codes() -> set[str]:
    """일별이 아니라 월간·분기로 발표되는 입력 — load_global_rate_features가
    이 코드들만 forward-fill한다(일별 시리즈는 그대로 둔다)."""
    from app.ingestion.jobs.ingest_housing_indicators import ALL_SERIES as HOUSING_ALL_SERIES

    return set(HOUSING_ALL_SERIES.keys())


def _forward_fill_monthly(observations: dict[date, float], daily_dates: list[date]) -> dict[date, float]:
    """월간 관측치를 다음 관측치가 나오기 전까지 유지하는 일별 시리즈로 바꾼다.

    daily_dates는 오름차순으로 정렬된 날짜 목록(다른 일별 자산들의 거래일)이다.
    각 daily_date에 대해 그 날짜 이전(이하)에 알려진 가장 최근 관측치를 쓴다 —
    아직 관측치가 없는 daily_date는 결과에서 제외한다(look-ahead 방지).
    """
    sorted_obs_dates = sorted(observations)
    filled: dict[date, float] = {}
    obs_idx = 0
    latest_value: float | None = None
    for d in daily_dates:
        while obs_idx < len(sorted_obs_dates) and sorted_obs_dates[obs_idx] <= d:
            latest_value = observations[sorted_obs_dates[obs_idx]]
            obs_idx += 1
        if latest_value is not None:
            filled[d] = latest_value
    return filled


@dataclass(frozen=True)
class GlobalRateFeatures:
    """모든 입력 자산이 공통으로 관측된 거래일만 남긴 정렬된 특징 패널."""

    codes: list[str]
    dates: list[date]
    values: np.ndarray  # (기간 × 자산), 전부 원 단위(bp 또는 지수) 그대로


def load_global_rate_features(
    db: Session, as_of: date, codes: list[str] | None = None, monthly_codes: set[str] | None = None
) -> GlobalRateFeatures:
    """as_of 시점에 알 수 있었던 금리·매크로 지표를 공통 거래일로 정렬해 가져온다.

    report_context.py의 load_price_history와 같은 구조(공통 거래일 교집합,
    visible_as_of point-in-time 필터)를 쓰지만, 여기서는 가격이 아니라 이미
    수준값(level)인 금리·지수를 그대로 쓴다 — 수익률로 변환하지 않는다.
    금리 수준 자체가 PCA-Ridge의 입력이기 때문이다(가격 계열과 다른 성격).

    codes를 생략하면 _all_input_codes()(실제 운영 자산: KTB1Y/KTB3Y + 미국
    금리곡선 15개 + 미국 주택 지표 9개)를 쓴다. monthly_codes를 생략하면
    _monthly_input_codes()(실제 운영 자산: 주택 지표 9개)를 쓴다. 둘 다
    테스트에서 격리된 목록을 주입할 수 있다 — 운영 DB의 KTB1Y/KTB3Y에
    의존하면 다른 테스트 파일의 teardown이 그 자산을 지웠을 때 실행 순서에
    따라 실패하는 취약한 테스트가 된다(2026-08 실측).
    """
    codes = codes if codes is not None else _all_input_codes()
    by_code: dict[str, dict[date, float]] = {}
    for code in codes:
        asset = db.query(DimAsset).filter_by(code=code).first()
        if asset is None:
            continue
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
        if rows:
            by_code[code] = {r.trade_date: float(r.close) for r in rows}

    present = [c for c in codes if c in by_code]
    if not present:
        return GlobalRateFeatures(codes=[], dates=[], values=np.empty((0, 0)))

    monthly_codes = monthly_codes if monthly_codes is not None else _monthly_input_codes()
    daily_present = [c for c in present if c not in monthly_codes]
    monthly_present = [c for c in present if c in monthly_codes]

    if not daily_present:
        # 전부 월간 지표뿐이면(테스트 등) 그대로 정확한 날짜 교집합으로 처리한다
        # — forward-fill할 일별 날짜 축 자체가 없다.
        common = set(by_code[present[0]])
        for code in present[1:]:
            common &= set(by_code[code])
        common_dates = sorted(common)
        values = np.array([[by_code[c][d] for c in present] for d in common_dates], dtype=float)
        return GlobalRateFeatures(codes=present, dates=common_dates, values=values)

    # 일별 시리즈끼리는 정확한 날짜 교집합, 월간 시리즈는 그 날짜 축에 맞춰
    # forward-fill한다 — 위 모듈 docstring 참고.
    common = set(by_code[daily_present[0]])
    for code in daily_present[1:]:
        common &= set(by_code[code])
    daily_common_dates = sorted(common)

    filled_monthly = {
        code: _forward_fill_monthly(by_code[code], daily_common_dates) for code in monthly_present
    }
    # forward-fill 후에도 시작 시점 이전(첫 관측치보다 이른 날짜)은 값이 없을 수
    # 있다 — 그런 날짜는 여전히 제외해야 하므로 다시 교집합을 구한다.
    common_dates = daily_common_dates
    for code in monthly_present:
        common_dates = [d for d in common_dates if d in filled_monthly[code]]

    values = np.array(
        [
            [by_code[c][d] for c in daily_present] + [filled_monthly[c][d] for c in monthly_present]
            for d in common_dates
        ],
        dtype=float,
    )
    return GlobalRateFeatures(codes=daily_present + monthly_present, dates=common_dates, values=values)


@dataclass(frozen=True)
class WalkForwardRidgeFit:
    """특정 origin 시점에 60개월 학습창으로 새로 적합한 계수 — 다음 origin에서 버려진다."""

    scaler_mean: np.ndarray
    scaler_scale: np.ndarray
    pca_components: np.ndarray
    pca_mean: np.ndarray
    ridge_coef: np.ndarray
    ridge_intercept: float
    n_training_rows: int


def _build_training_set(
    features: GlobalRateFeatures, target_col: int, horizon: int = PREDICTION_HORIZON_TRADING_DAYS
) -> tuple[np.ndarray, np.ndarray]:
    """X_t = features[t], y_t = target[t+horizon] - target[t]. 라벨이 없는 마지막
    horizon개 행은 제외한다(위 모듈 docstring 'label maturity' 참고)."""
    n = features.values.shape[0]
    usable = n - horizon
    if usable <= 0:
        return np.empty((0, features.values.shape[1])), np.empty(0)
    x = features.values[:usable]
    target = features.values[:, target_col]
    y = target[horizon : horizon + usable] - target[:usable]
    return x, y


def fit_walk_forward_ridge(
    features: GlobalRateFeatures,
    *,
    n_components: int = PCA_COMPONENTS,
    alpha: float = RIDGE_ALPHA,
    training_window: int = TRAINING_WINDOW_TRADING_DAYS,
    target_code: str = TARGET_CODE,
) -> WalkForwardRidgeFit | None:
    """가장 최근 학습창(최대 training_window행)만 써서 표준화·PCA·Ridge를 새로 적합한다.

    CallRank의 고정 헤지(fit_frozen_hedge)와 달리 매 호출마다 처음부터
    재적합한다 — 계수를 어디에도 캐싱하지 않는다(방법론상 매월 새로
    학습해야 하므로).
    """
    target_col = features.codes.index(target_code)
    x_full, y_full = _build_training_set(features, target_col)
    if len(y_full) == 0:
        return None

    x_train = x_full[-training_window:]
    y_train = y_full[-training_window:]
    if len(y_train) < MIN_OBSERVATIONS_FOR_TRAINING - PREDICTION_HORIZON_TRADING_DAYS:
        return None

    scaler = StandardScaler().fit(x_train)
    x_scaled = scaler.transform(x_train)

    k = min(n_components, x_scaled.shape[0], x_scaled.shape[1])
    pca = PCA(n_components=k).fit(x_scaled)
    x_pca = pca.transform(x_scaled)

    ridge = Ridge(alpha=alpha).fit(x_pca, y_train)

    return WalkForwardRidgeFit(
        scaler_mean=scaler.mean_,
        scaler_scale=scaler.scale_,
        pca_components=pca.components_,
        pca_mean=pca.mean_,
        ridge_coef=ridge.coef_,
        ridge_intercept=float(ridge.intercept_),
        n_training_rows=len(y_train),
    )


def predict_latest(features: GlobalRateFeatures, fit: WalkForwardRidgeFit) -> float:
    """가장 최근 관측치(features.values[-1])에 적합된 계수를 적용해 향후
    PREDICTION_HORIZON_TRADING_DAYS의 KTB3Y 변화(bp)를 예측한다."""
    x_latest = features.values[-1:]
    x_scaled = (x_latest - fit.scaler_mean) / fit.scaler_scale
    x_centered = x_scaled - fit.pca_mean
    x_pca = x_centered @ fit.pca_components.T
    prediction = x_pca @ fit.ridge_coef + fit.ridge_intercept
    return float(prediction[0])


def predict_change_bp(db: Session, as_of: date) -> float | None:
    """as_of 시점까지 알 수 있던 데이터로 향후 63거래일 KTB3Y 변화(bp)를 예측한다.

    이력이 부족하면(60개월 학습창을 채울 수 없음) None을 반환한다 — 숫자를
    만들어내지 않는다. 호출부(city_ai_stub.py)가 None일 때 합성값으로
    폴백한다.
    """
    features = load_global_rate_features(db, as_of)
    if not features.codes or TARGET_CODE not in features.codes:
        return None
    if features.values.shape[0] < MIN_OBSERVATIONS_FOR_TRAINING:
        return None

    fit = fit_walk_forward_ridge(features)
    if fit is None:
        return None

    return predict_latest(features, fit)
