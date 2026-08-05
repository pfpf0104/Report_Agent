"""PCA-Ridge 예측 모델 — 격리된 합성 자산으로 검증한다(운영 KTB1Y/KTB3Y에 의존하지 않음).

이전 버전은 운영 DB의 실제 KTB1Y/KTB3Y/미국 금리곡선 데이터에 의존했는데,
같은 스위트 안의 다른 테스트 파일(test_ingest_macro_rates.py 등)이
setup/teardown에서 그 DimAsset 자체를 지워버려 실행 순서에 따라 실패하는
취약한 테스트였다(2026-08 실측 재현). load_global_rate_features와
fit_walk_forward_ridge에 codes/target_code 주입 파라미터를 추가해, 이
테스트 파일 전용 격리된 자산 코드(_GRM_ 접두사)로 완전히 독립적으로
검증한다.

여기서 볼 것: 1) 이력 부족 시 None을 반환하는가(합성값을 만들지 않는가),
2) point-in-time 필터가 실제로 미래 데이터를 차단하는가, 3) 라벨 성숙
구간(마지막 63거래일)을 제외하고 학습하는가, 4) 학습·예측 파이프라인이
차원 오류 없이 끝까지 도는가.
"""
from datetime import date, timedelta

import numpy as np
import pytest

from app.computation.fixed_income.global_rate_model import (
    MIN_OBSERVATIONS_FOR_TRAINING,
    PREDICTION_HORIZON_TRADING_DAYS,
    TRAINING_WINDOW_TRADING_DAYS,
    GlobalRateFeatures,
    _build_training_set,
    _forward_fill_monthly,
    fit_walk_forward_ridge,
    load_global_rate_features,
    predict_latest,
)
from app.db.base import SessionLocal
from app.db.models.dim_asset import AssetType, DimAsset
from app.db.models.fact_market_daily import FactMarketDaily

TEST_TARGET_CODE = "_GRM_TARGET"
TEST_INPUT_CODES = [TEST_TARGET_CODE] + [f"_GRM_F{i}" for i in range(16)]
START = date(2021, 1, 4)


def _cleanup(session):
    ids = session.query(DimAsset.asset_id).filter(DimAsset.code.in_(TEST_INPUT_CODES))
    session.query(FactMarketDaily).filter(FactMarketDaily.asset_id.in_(ids)).delete(
        synchronize_session=False
    )
    session.query(DimAsset).filter(DimAsset.code.in_(TEST_INPUT_CODES)).delete(synchronize_session=False)
    session.commit()


@pytest.fixture()
def db():
    session = SessionLocal()
    _cleanup(session)
    yield session
    _cleanup(session)
    session.close()


def _business_days(n: int, start: date = START) -> list[date]:
    days, d = [], start
    while len(days) < n:
        if d.weekday() < 5:
            days.append(d)
        d += timedelta(days=1)
    return days


def _seed(session, code: str, dates: list[date], values, *, knowledge_offset: int = 0) -> None:
    asset = DimAsset(asset_type=AssetType.MACRO.value, code=code, name_kr=code, currency="USD")
    session.add(asset)
    session.commit()
    session.refresh(asset)
    session.bulk_save_objects([
        FactMarketDaily(
            asset_id=asset.asset_id, trade_date=d,
            knowledge_date=d + timedelta(days=knowledge_offset),
            close=float(v), adj_close=float(v), source="test",
        )
        for d, v in zip(dates, values)
    ])
    session.commit()


def _rate_path(n: int, seed: int, base: float = 300.0, vol: float = 3.0):
    rng = np.random.default_rng(seed)
    return base + np.cumsum(rng.normal(0, vol, size=n))


def _seed_full_universe(session, n_days: int, *, seed_base: int = 1000) -> list[date]:
    dates = _business_days(n_days)
    for i, code in enumerate(TEST_INPUT_CODES):
        _seed(session, code, dates, _rate_path(n_days, seed_base + i))
    return dates


# --- load_global_rate_features (codes 주입, 격리) ------------------------------


def test_load_global_rate_features_returns_empty_when_no_data(db):
    features = load_global_rate_features(db, date(2026, 8, 1), codes=TEST_INPUT_CODES)
    assert features.codes == []
    assert features.values.shape == (0, 0)


def test_load_global_rate_features_respects_point_in_time_cutoff(db):
    """as_of보다 미래에 알려진(knowledge_date) 값은 features에 포함되면 안 된다."""
    n = 20
    dates = _business_days(n)
    _seed(db, TEST_TARGET_CODE, dates, _rate_path(n, 1), knowledge_offset=365)

    hidden = load_global_rate_features(db, dates[-1], codes=[TEST_TARGET_CODE])
    visible = load_global_rate_features(db, dates[-1] + timedelta(days=365), codes=[TEST_TARGET_CODE])

    assert hidden.values.shape[0] == 0
    assert visible.values.shape[0] == n


def test_load_global_rate_features_keeps_only_common_dates(db):
    dates = _business_days(10)
    _seed(db, TEST_TARGET_CODE, dates, _rate_path(10, 1))
    _seed(db, "_GRM_F0", dates[:6], _rate_path(6, 2))  # 4일 짧다

    features = load_global_rate_features(db, date(2099, 1, 1), codes=[TEST_TARGET_CODE, "_GRM_F0"])

    assert features.values.shape[0] == 6


def test_load_global_rate_features_forward_fills_monthly_codes_onto_daily_dates(db):
    """일별 시리즈(_GRM_TARGET)와 월간 시리즈(_GRM_F0, monthly_codes로 지정)를
    섞으면, 월간 값이 정확한 날짜 교집합이 아니라 forward-fill로 합쳐져야 한다
    — 그렇지 않으면 공통 거래일이 사실상 0에 가까워진다(모듈 docstring 참고)."""
    daily_dates = _business_days(40)  # 매 거래일 값이 있는 일별 시리즈
    _seed(db, TEST_TARGET_CODE, daily_dates, [100.0] * 40)

    # 월간 관측치: 첫 거래일과 20번째 거래일에만 값이 있다(한 달에 1번꼴 근사).
    monthly_dates = [daily_dates[0], daily_dates[20]]
    _seed(db, "_GRM_F0", monthly_dates, [10.0, 20.0])

    features = load_global_rate_features(
        db, daily_dates[-1], codes=[TEST_TARGET_CODE, "_GRM_F0"], monthly_codes={"_GRM_F0"}
    )

    # forward-fill 덕에 첫 관측치 이후 모든 거래일이 살아남아야 한다(정확한
    # 날짜 교집합이었다면 2일만 남았을 것).
    assert features.values.shape[0] == 40
    monthly_col = features.codes.index("_GRM_F0")
    values_by_date = dict(zip(features.dates, features.values[:, monthly_col]))
    assert values_by_date[daily_dates[0]] == 10.0
    assert values_by_date[daily_dates[10]] == 10.0  # 다음 관측치 전까지 이전 값 유지
    assert values_by_date[daily_dates[20]] == 20.0
    assert values_by_date[daily_dates[-1]] == 20.0


# --- 월간 지표 forward-fill (순수 함수, DB 불필요) ------------------------------


def test_forward_fill_monthly_carries_value_until_next_observation():
    observations = {date(2024, 1, 31): 100.0, date(2024, 2, 29): 105.0}
    daily_dates = [date(2024, 1, 31), date(2024, 2, 1), date(2024, 2, 15), date(2024, 2, 29), date(2024, 3, 1)]

    filled = _forward_fill_monthly(observations, daily_dates)

    assert filled[date(2024, 1, 31)] == 100.0
    assert filled[date(2024, 2, 1)] == 100.0  # 다음 관측치(2/29) 전까지 1/31 값 유지
    assert filled[date(2024, 2, 15)] == 100.0
    assert filled[date(2024, 2, 29)] == 105.0  # 새 관측치 반영
    assert filled[date(2024, 3, 1)] == 105.0


def test_forward_fill_monthly_excludes_dates_before_first_observation():
    """look-ahead 방지 — 첫 관측치보다 이른 날짜는 아직 아무것도 몰랐으므로
    결과에 아예 없어야 한다(0이나 다른 값으로 채우면 안 된다)."""
    observations = {date(2024, 2, 29): 105.0}
    daily_dates = [date(2024, 1, 1), date(2024, 1, 31), date(2024, 2, 29)]

    filled = _forward_fill_monthly(observations, daily_dates)

    assert date(2024, 1, 1) not in filled
    assert date(2024, 1, 31) not in filled
    assert filled[date(2024, 2, 29)] == 105.0


def test_forward_fill_monthly_empty_observations_returns_empty():
    assert _forward_fill_monthly({}, [date(2024, 1, 1), date(2024, 1, 2)]) == {}


# --- 학습 세트 구성 (순수 함수, DB 불필요) -------------------------------------


def test_build_training_set_excludes_unmatured_label_tail():
    """마지막 horizon개 행은 라벨(미래 타깃값)이 없으므로 학습셋에서 빠져야 한다."""
    n = 100
    horizon = 10
    values = np.column_stack([np.arange(n, dtype=float), np.arange(n, dtype=float) * 2])
    features = GlobalRateFeatures(codes=["A", "TARGET"], dates=[date(2021, 1, 1)] * n, values=values)

    x, y = _build_training_set(features, target_col=1, horizon=horizon)

    assert x.shape[0] == n - horizon
    assert y.shape[0] == n - horizon


def test_build_training_set_label_matches_manual_difference():
    n = 50
    horizon = 5
    target = np.array([100.0 + i for i in range(n)])
    values = np.column_stack([np.zeros(n), target])
    features = GlobalRateFeatures(codes=["A", "TARGET"], dates=[date(2021, 1, 1)] * n, values=values)

    x, y = _build_training_set(features, target_col=1, horizon=horizon)

    # target은 등차수열(+1/일)이므로 horizon일 뒤 변화는 항상 +horizon
    assert np.allclose(y, float(horizon))


def test_build_training_set_returns_empty_when_horizon_exceeds_data():
    n = 5
    horizon = 10
    values = np.zeros((n, 2))
    features = GlobalRateFeatures(codes=["A", "TARGET"], dates=[date(2021, 1, 1)] * n, values=values)

    x, y = _build_training_set(features, target_col=1, horizon=horizon)

    assert x.shape[0] == 0
    assert y.shape[0] == 0


# --- 워크포워드 적합·예측 파이프라인 (DB 왕복, 격리된 코드) --------------------


def test_fit_walk_forward_ridge_returns_none_when_insufficient_rows(db):
    n = 50
    dates = _seed_full_universe(db, n)
    features = load_global_rate_features(db, dates[-1], codes=TEST_INPUT_CODES)

    fit = fit_walk_forward_ridge(features, training_window=1000, target_code=TEST_TARGET_CODE)

    assert fit is None


def test_fit_walk_forward_ridge_and_predict_succeeds_with_sufficient_history(db):
    n = MIN_OBSERVATIONS_FOR_TRAINING + 100
    dates = _seed_full_universe(db, n)
    features = load_global_rate_features(db, dates[-1], codes=TEST_INPUT_CODES)

    assert features.values.shape[0] >= MIN_OBSERVATIONS_FOR_TRAINING

    fit = fit_walk_forward_ridge(features, target_code=TEST_TARGET_CODE)

    assert fit is not None
    n_components = fit.pca_components.shape[0]
    assert fit.ridge_coef.shape == (n_components,)
    assert fit.pca_mean.shape[0] == len(features.codes)

    prediction = predict_latest(features, fit)
    assert isinstance(prediction, float)


def test_fit_walk_forward_ridge_uses_only_the_trailing_window(db):
    """학습창 밖의 아주 오래된 이상치가 계수에 영향을 주면 안 된다 — 워크포워드는
    매번 "최근 N개월만" 재적합하는 것이 핵심이다."""
    n = MIN_OBSERVATIONS_FOR_TRAINING + 500
    dates = _seed_full_universe(db, n)
    features = load_global_rate_features(db, dates[-1], codes=TEST_INPUT_CODES)
    # 아주 앞부분(학습창 밖)에 극단값을 심는다.
    features.values[:100] *= 1000

    fit = fit_walk_forward_ridge(features, target_code=TEST_TARGET_CODE)

    assert fit is not None
    prediction = predict_latest(features, fit)
    # 극단값이 계수에 반영됐다면 예측이 비정상적으로 커진다.
    assert abs(prediction) < 1000


def test_predict_change_bp_wiring_matches_manual_pipeline(db):
    """predict_change_bp(실제 운영 함수)와 동일한 알고리즘을, 이 파일의 격리된
    코드로 직접 조립했을 때 같은 형태의 결과가 나오는지 확인한다 — 운영
    함수 자체는 KTB1Y/KTB3Y에 고정돼 있어 여기서 직접 호출할 수 없으므로,
    load_global_rate_features → fit_walk_forward_ridge → predict_latest
    체인이 predict_change_bp 내부 로직과 같은 방식으로 성공하는지를 본다."""
    n = MIN_OBSERVATIONS_FOR_TRAINING + 100
    dates = _seed_full_universe(db, n)

    features = load_global_rate_features(db, dates[-1], codes=TEST_INPUT_CODES)
    if not features.codes or TEST_TARGET_CODE not in features.codes:
        pytest.fail("target code missing from loaded features")
    if features.values.shape[0] < MIN_OBSERVATIONS_FOR_TRAINING:
        pytest.fail("insufficient observations")

    fit = fit_walk_forward_ridge(features, target_code=TEST_TARGET_CODE)
    assert fit is not None
    result = predict_latest(features, fit)

    assert isinstance(result, float)
