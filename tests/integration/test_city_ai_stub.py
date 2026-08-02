"""city_ai_stub — G13(금리커브 실측 우선) + G4(predicted_change_bp 실측 우선) 검증.

DB 왕복이 필요해 통합 테스트로 둔다. 여기서 확인할 것은 네 가지다.
  1) DB에 KTB1Y/KTB3Y가 있으면 그 bp 값을 그대로 쓰는가(단위 변환을 다시 하지 않는가).
  2) DB에 없으면(백필 전 구간) 합성 폴백으로 조용히 넘어가는가.
  3) 이 fixture(KTB1Y/KTB3Y만 seed, 미국 금리곡선 없음)에서는 PCA-Ridge 모델이
     학습 불가능하므로 predicted_change_bp가 여전히 합성이어야 한다.
"실제 운영 DB에서 predict_change_bp를 우선하는가"는 이 파일의 db fixture가
매 테스트마다 KTB1Y/KTB3Y를 지워 운영 이력을 요구하는 검증과 공존할 수 없다
— test_city_ai_stub_real_model_wiring.py에서 별도로 확인한다.
"""
from datetime import date

import pytest

from app.computation.fixed_income.city_ai_stub import synthetic_city_ai_output
from app.db.base import SessionLocal
from app.db.models.dim_asset import AssetType, DimAsset
from app.db.models.fact_market_daily import FactMarketDaily

CODES = ["KTB1Y", "KTB3Y"]

# 실제 인제스천(백필 포함)이 절대 채우지 않을 먼 미래 날짜를 쓴다 — 실제
# 영업일(예: 2026-07-15)을 쓰면 운영 백필이 이미 그 날짜에 실측값을 넣어둔
# 상태와 충돌한다(unique(asset_id, trade_date) 위반, 2026-08 실측 재현).
FAR_FUTURE_DATE = date(2099, 12, 31)


def _cleanup(session):
    """이 파일이 심은(source="test") 행만 지운다 — DimAsset 자체와 다른 소스의
    행(예: 운영 인제스천이 넣은 bok_ecos/bok_ecos_backfill)은 건드리지 않는다.
    KTB1Y/KTB3Y는 이 프로젝트 실제 운영 자산 코드라 예전엔 DimAsset까지
    지웠는데, 다른 테스트 파일이 같은 시점에 실측 KTB1Y/KTB3Y를 요구하는
    경우(test_city_ai_stub_real_model_wiring.py) 그 데이터를 지워버려 실패를
    유발했다(2026-08 실측 재현) — 이 fixture 자체가 만든 것만 지우도록 좁힌다.
    """
    ids = session.query(DimAsset.asset_id).filter(DimAsset.code.in_(CODES))
    session.query(FactMarketDaily).filter(
        FactMarketDaily.asset_id.in_(ids), FactMarketDaily.source == "test"
    ).delete(synchronize_session=False)
    session.commit()


@pytest.fixture()
def db():
    session = SessionLocal()
    _cleanup(session)
    yield session
    _cleanup(session)
    session.close()


def _get_or_create_asset(session, code: str) -> DimAsset:
    asset = session.query(DimAsset).filter_by(code=code).first()
    if asset is not None:
        return asset
    asset = DimAsset(asset_type=AssetType.MACRO.value, code=code, name_kr=code, currency="KRW")
    session.add(asset)
    session.commit()
    session.refresh(asset)
    return asset


def _seed_rate(session, code: str, trade_date: date, bp_value: float) -> None:
    asset = _get_or_create_asset(session, code)
    session.add(
        FactMarketDaily(
            asset_id=asset.asset_id,
            trade_date=trade_date,
            knowledge_date=trade_date,
            close=bp_value,
            adj_close=bp_value,
            source="test",
        )
    )
    session.commit()


def test_uses_real_rates_when_present_in_bp_without_reconverting(db):
    """DB의 KTB1Y/KTB3Y는 이미 bp로 정규화돼 있다(ingest_macro_rates.py) —
    이 함수가 다시 ×100 하면 100배 오류가 조용히 생긴다(G13이 실제로
    노출되는 경로)."""
    as_of = FAR_FUTURE_DATE
    _seed_rate(db, "KTB1Y", as_of, 336.5)
    _seed_rate(db, "KTB3Y", as_of, 375.8)

    out = synthetic_city_ai_output(db, as_of)

    assert out["yield_1y_bp"] == 336.5
    assert out["yield_3y_bp"] == 375.8


def test_falls_back_to_synthetic_when_no_real_data(db):
    """백필 전 구간이나 자산이 아예 없으면 합성값으로 조용히 폴백해야 한다 —
    예외를 던지면 그 시점 리포트 전체가 죽는다."""
    as_of = FAR_FUTURE_DATE

    out = synthetic_city_ai_output(db, as_of)

    assert isinstance(out["yield_1y_bp"], float)
    assert isinstance(out["yield_3y_bp"], float)


def test_falls_back_when_db_is_none():
    """db=None(순수 계산 경로 테스트 등)이면 즉시 합성값을 쓴다 — DB 접근을
    시도하지 않는다."""
    out = synthetic_city_ai_output(None, FAR_FUTURE_DATE)

    assert isinstance(out["yield_1y_bp"], float)
    assert isinstance(out["yield_3y_bp"], float)


def test_predicted_change_stays_synthetic_when_global_rate_model_has_no_data():
    """미국 금리곡선(ingest_global_rates.py 자산)이 전혀 없는 시점에서는
    PCA-Ridge 모델이 학습 불가능하므로 predicted_change_bp는 금리커브 실측
    유무와 무관하게 여전히 합성값이어야 한다(두 입력의 책임이 섞이지 않는다).

    FAR_FUTURE_DATE(2099-12-31)를 as_of로 쓰면 운영 DB의 모든 실측 데이터
    (KTB1Y/KTB3Y + 미국 금리곡선 5년치)가 visible_as_of를 전부 통과해버려
    "미국 금리곡선이 없다"는 전제가 깨진다(2026-08 실측 재현 — 이 시점에는
    predict_change_bp가 실제로 성공해 버려 이 테스트의 취지와 어긋난다).
    대신 미국 금리곡선 인제스천이 시작되기 전(2021-08-03 이전) 시점을 써서
    "그 시점엔 아직 없었다"는 자연스러운 이력 부족을 재현한다 — db fixture를
    쓰지 않고 KTB1Y/KTB3Y도 실제 운영 데이터(2021-01-04부터 있음)를 그대로
    쓴다."""
    from app.db.base import SessionLocal

    as_of = date(2021, 3, 1)  # KTB1Y/KTB3Y는 있지만 미국 금리곡선은 아직 없는 구간
    db = SessionLocal()
    try:
        out = synthetic_city_ai_output(db, as_of)
    finally:
        db.close()

    # 이 시점 KTB1Y/KTB3Y 실측이 있다면 yield는 실측을 쓰지만, 미국 금리곡선이
    # 없어 예측 모델은 반드시 폴백(결정적 시드 합성값)이어야 한다.
    import numpy as np

    rng = np.random.default_rng(as_of.toordinal())
    rng.normal(0, 5)  # fallback_1y 소비(synthetic_city_ai_output과 같은 순서)
    rng.normal(20, 5)  # fallback_3y 소비
    expected_synthetic_prediction = rng.normal(5, 15)

    assert out["predicted_change_bp"] == pytest.approx(expected_synthetic_prediction)


def test_partial_real_data_only_overrides_the_available_leg(db):
    """1년물만 실측이 있고 3년물이 없으면, 1년물만 실측을 쓰고 3년물은 폴백해야
    한다 — 한쪽이 없다고 둘 다 폴백으로 넘어가면 이미 있는 실측을 버리는 것이다."""
    as_of = FAR_FUTURE_DATE
    _seed_rate(db, "KTB1Y", as_of, 336.5)

    out = synthetic_city_ai_output(db, as_of)

    assert out["yield_1y_bp"] == 336.5
    assert isinstance(out["yield_3y_bp"], float)
    assert out["yield_3y_bp"] != 336.5


def test_respects_point_in_time_cutoff(db):
    """as_of보다 미래에 알려진(knowledge_date) 값은 보이면 안 된다 — MetroGuard의
    다른 모든 입력과 동일한 point-in-time 규율을 city_ai_stub도 지켜야 한다."""
    from datetime import timedelta

    trade_date = FAR_FUTURE_DATE
    asset = _get_or_create_asset(db, "KTB1Y")
    # 운영 DB의 실제 최신 KTB1Y 값과 우연히 겹치면(2026-08 실측 재현: 최신
    # 실측값이 정확히 336.5였다) "아직 안 보여야 하므로 폴백" 단정이 우연히
    # 실패한다 — 실측값 범위(수백bp) 밖의 값을 써서 우연한 일치를 원천 차단한다.
    distinctive_value = 999999.0
    db.add(
        FactMarketDaily(
            asset_id=asset.asset_id,
            trade_date=trade_date,
            knowledge_date=trade_date + timedelta(days=30),
            close=distinctive_value,
            adj_close=distinctive_value,
            source="test",
        )
    )
    db.commit()

    out_before = synthetic_city_ai_output(db, trade_date)
    out_after = synthetic_city_ai_output(db, trade_date + timedelta(days=30))

    assert out_before["yield_1y_bp"] != distinctive_value  # 아직 안 보여야 하므로 폴백
    assert out_after["yield_1y_bp"] == distinctive_value
