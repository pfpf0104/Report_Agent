"""City AI 스텁 — G4 부분 해소: predicted_change_bp도 이제 실측 PCA-Ridge 모델을 우선한다.

City AI는 한국 금리곡선(19개 입력)·글로벌 금리(21개)·미국 전국 주택(3개)·
미국 도시 주택(6개), 총 49개 입력을 60개월 워크포워드로 매월 처음부터
재적합하고, PCA-8 압축 후 penalty λ=10 고정 Ridge로 향후 63거래일 한국
3년 금리변화를 예측한다(첨부 MetroGuard-KR 보고서 7페이지).

app/computation/fixed_income/global_rate_model.py가 이 방법론(표준화→PCA-8→
Ridge λ=10)을 한국 금리 2개 + 미국 금리곡선·매크로 지표 15개 + 미국 주택
지표 9개(총 26개 입력, 원본의 49개에는 못 미침 — 원본 보고서 PDF가 저장소에
없어 정확히 같은 시리즈는 아니다, ingest_housing_indicators.py docstring
참고)로 실제 구현했다. 이 함수는 그 모델을 우선 호출하고, 실패하면(이력
부족·자산 없음) 합성값으로 폴백한다 — RIM의 KIS 현재가/DART BPS와 동일한
실측 우선 패턴이다.

yield_1y_bp/yield_3y_bp(현재 금리커브)도 같은 원칙 — 이건 "예측"이 아니라
"지금 알려진 값"이라 BOK ECOS(ingest_macro_rates.py, KTB1Y/KTB3Y)가 이미
매일 채우고 있다. DB에 해당 as_of 시점 값이 없으면(백필 전 구간·주말 등)
합성값으로 폴백한다.

단위 규약(G13): fact_market_daily에 저장된 KTB1Y/KTB3Y는 bp다
(ingest_macro_rates.py가 BOK의 %를 ×100 정규화). global_rate_model.py가
반환하는 predicted_change_bp도 같은 단위(bp)다 — 학습 타깃 자체가
"KTB3Y(bp) 변화량"이라 별도 변환이 필요 없다. 이 함수가 반환하는 세 값
모두 폴백이든 실측이든 항상 bp다.
"""
from __future__ import annotations

from datetime import date

import numpy as np
from sqlalchemy.orm import Session

from app.db.models.dim_asset import DimAsset
from app.db.models.fact_market_daily import FactMarketDaily
from app.db.point_in_time import visible_as_of

MACRO_ASSET_CODE = {"1y": "KTB1Y", "3y": "KTB3Y"}


def _latest_yield_bp(db: Session, code: str, as_of: date) -> float | None:
    asset = db.query(DimAsset).filter_by(code=code).first()
    if asset is None:
        return None
    row = (
        visible_as_of(db.query(FactMarketDaily), FactMarketDaily, as_of)
        .filter_by(asset_id=asset.asset_id)
        .order_by(FactMarketDaily.trade_date.desc())
        .first()
    )
    if row is None or row.close is None:
        return None
    return float(row.close)  # ingest_macro_rates.py가 이미 bp로 정규화해 저장


def synthetic_city_ai_output(db: Session | None, as_of: date, seed: int | None = None) -> dict:
    rng = np.random.default_rng(seed if seed is not None else as_of.toordinal())

    fallback_1y = 300.0 + rng.normal(0, 5)
    fallback_3y = 300.0 + rng.normal(20, 5)  # 정상(우상향) 커브를 기본 가정
    predicted_change_bp = rng.normal(5, 15)  # 63거래일 뒤 3년 금리 변화 예측(약한 상승 편향, 여전히 합성)

    yield_1y_bp = fallback_1y
    yield_3y_bp = fallback_3y
    if db is not None:
        real_1y = _latest_yield_bp(db, MACRO_ASSET_CODE["1y"], as_of)
        real_3y = _latest_yield_bp(db, MACRO_ASSET_CODE["3y"], as_of)
        if real_1y is not None:
            yield_1y_bp = real_1y
        if real_3y is not None:
            yield_3y_bp = real_3y

        # G4 부분 해소: PCA-Ridge 모델이 실제 예측을 낼 수 있으면 그걸 쓴다.
        # 지연 import — city_ai_stub이 계산 계층 최하단(순환 위험 최소화 대상)
        # 이라 global_rate_model을 모듈 로드 시점에 끌어오지 않는다.
        from app.computation.fixed_income.global_rate_model import predict_change_bp

        real_prediction = predict_change_bp(db, as_of)
        if real_prediction is not None:
            predicted_change_bp = real_prediction

    return {
        "yield_1y_bp": float(yield_1y_bp),
        "yield_3y_bp": float(yield_3y_bp),
        "predicted_change_bp": float(predicted_change_bp),
    }
