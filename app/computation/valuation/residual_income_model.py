"""삼성전자/SK하이닉스 밸류에이션 리포트 context 빌더.

TODO: 5년 전환형 잔여이익모형(RIM) — 시나리오별 ROE 경로·자기자본비용·
총지급률로 적정가치를 계산하고 fact_financial_quarterly에서 실제 BPS/EPS를
조회하는 로직을 구현한다. 지금은 rendering 파이프라인과 data-table 컴포넌트를
검증하기 위한 자리표시 값만 반환한다.
"""
from __future__ import annotations

from datetime import date

from sqlalchemy.orm import Session


def build_valuation_context(db: Session, as_of: date) -> dict:
    return {
        "title": "한국 주식시장과 대표 반도체 기업의 적정가치 평가",
        "subtitle": "저(低) 포워드 PER의 착시 · 중국 메모리 부상 · 시나리오별 의사결정",
        "meta_lines": [
            f"보고서 기준일 {as_of.isoformat()}",
            "KOSPI 종가 및 MSCI Korea 최신 공개지표",
        ],
        "cards": [
            {"label": "한국시장", "value": "N/A", "caption": "RIM 모델 미구현", "tone": None},
        ],
        "table": {
            "columns": ["대상", "현재", "중앙 적정범위", "판단"],
            "rows": [
                ["한국시장", "N/A", "N/A", "TODO: residual_income_model 구현"],
            ],
        },
        "source": "독립 투자분석 보고서 (placeholder)",
    }
