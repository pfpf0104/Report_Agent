"""TODO(실데이터 연동): 실제 City AI로 교체.

City AI는 한국 금리곡선(19개 입력)·글로벌 금리(21개)·미국 전국 주택(3개)·
미국 도시 주택(6개), 총 49개 입력을 60개월 워크포워드로 매월 처음부터
재적합하고, PCA-8 압축 후 penalty λ=10 고정 Ridge로 향후 63거래일 한국
3년 금리변화를 예측한다(첨부 MetroGuard-KR 보고서 7페이지). 이걸 그대로
재현하려면 BOK 금리곡선, FRED 글로벌 금리, Zillow ZHVI 실데이터와 API
키가 필요하므로 범위 밖이다(ingestion 연동 대기 — DART/BOK/KIS 태스크).

지금은 결정적 시드로 그럴듯한 예측치와 현재 금리커브를 만든다. 목적은
duration_controller.py의 carry-price gate·tanh 경고·lot ledger가 실제
City AI 출력이 들어와도 그대로 맞물려 동작하는지 검증하는 것이다.
"""
from __future__ import annotations

from datetime import date

import numpy as np


def synthetic_city_ai_output(as_of: date, seed: int | None = None) -> dict:
    rng = np.random.default_rng(seed if seed is not None else as_of.toordinal())

    yield_1y_bp = 300.0 + rng.normal(0, 5)
    yield_3y_bp = 300.0 + rng.normal(20, 5)  # 정상(우상향) 커브를 기본 가정
    predicted_change_bp = rng.normal(5, 15)  # 63거래일 뒤 3년 금리 변화 예측(약한 상승 편향)

    return {
        "yield_1y_bp": float(yield_1y_bp),
        "yield_3y_bp": float(yield_3y_bp),
        "predicted_change_bp": float(predicted_change_bp),
    }
