"""MetroGuard-KR 리포트 context 빌더.

TODO: carry-price gate(A_t), tanh 기반 신규 경고(g_t), 목표 듀레이션(D*_t)
계산과 fact_market_daily(한국 채권지수)·fact_financial_quarterly(글로벌 금리
프록시) 조회 로직을 구현한다. 지금은 rendering 파이프라인과 디자인 시스템을
검증하기 위한 자리표시 값만 반환한다.
"""
from __future__ import annotations

from datetime import date

from sqlalchemy.orm import Session


def build_metroguard_context(db: Session, as_of: date) -> dict:
    return {
        "title": "8월 예비 운용안",
        "subtitle": "7월 공개 City AI 경고를 3년 목표 듀레이션으로 사전 점검",
        "meta_lines": [
            f"SHADOW 점검 {as_of.isoformat()}",
            "월말형 SHADOW · Convention C",
            "한국 채권지수 슬리브",
        ],
        "headline": "단축 경고 OFF · D3 중립 유지",
        "headline_body": (
            "TODO: duration_controller의 carry-price gate 계산 결과로 대체 예정. "
            "현재는 자리표시 문구입니다."
        ),
        "cards": [
            {"label": "동결 연구 누적수익", "value": "N/A", "caption": "모델 미구현", "tone": None},
        ],
        "source": "MetroGuard-KR · 월말 운용·연구 보고서 (placeholder)",
    }
