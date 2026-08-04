"""산업·경쟁 분석 — MASTER_PLAN Phase 4-4.

CFA Institute 표준 리서치 리포트의 필수 섹션 중 밸류에이션 리포트에 없던
부분이다. 기존 "INDUSTRY CYCLE CONTEXT" 페이지(residual_income_model.py의
CYCLE_SCENARIO_CARDS)는 D램 사이클 국면만 다루고 경쟁사 비교가 없었다.

## 무엇이 실측이고 무엇이 정성적 참고인가

- **장부가치(BPS) 비교는 실측이다.** 삼성전자·SK하이닉스는 DART
  사업보고서(`ingest_financial_statements.py`), 마이크론은 FMP
  key-metrics(`ingest_micron_financials.py`)에서 각각 실제로 조회한다.
- **ROE는 이 페이지에서 비교하지 않는다.** 삼성전자·SK하이닉스의 RIM
  시나리오 ROE(residual_income_model.py의 *_SCENARIOS)는 미래 예측
  가정치이지 실적이 아니고, DART 응답에서 실측 ROE를 뽑는 파이프라인은
  아직 없다(당기순이익 계정 미추출) — 마이크론(FMP 실측 ROE)과 나란히
  놓으면 "실측 대 가정"을 "실측 대 실측"처럼 보이게 만드는 오해를 유발한다.
  그래서 ROE는 이 페이지에 넣지 않는다.
- **시장 점유율·산업 구조(과점 3사 체제 등)는 이 프로젝트에 실측 소스가
  없다.** 정성적 참고로만 서술하고 출처를 명시한다(공개 업계 리포트 요약
  수준 — 통계적으로 추정한 값이 아니다).
"""
from __future__ import annotations

from datetime import date

from sqlalchemy.orm import Session

from app.db.models.dim_asset import DimAsset
from app.db.models.fact_financial_quarterly import FactFinancialQuarterly
from app.db.point_in_time import visible_as_of

MICRON_SYMBOL = "MU"

# 정성적 산업 구조 서술 — 실측 데이터가 없는 항목이라 출처를 명시하고, 통계적으로
# 추정한 수치가 아님을 분명히 한다.
INDUSTRY_STRUCTURE_CARDS = [
    {
        "title": "메모리 반도체 3강 과점 구조",
        "body": (
            "D램은 삼성전자·SK하이닉스·마이크론 3사가 전 세계 생산능력 대부분을 "
            "점유하는 과점 시장이다(각 사 IR 자료·업계 리포트에서 공통적으로 "
            "인용되는 구조 — 이 프로젝트가 시장 점유율을 직접 집계하지는 않는다)."
        ),
    },
    {
        "title": "진입장벽",
        "body": (
            "미세공정 전환에 필요한 자본지출 규모와 수율 확보 난이도가 신규 "
            "진입을 사실상 차단한다 — 중국 업체의 범용 제품 진입은 있으나 "
            "최선단 공정 격차는 유지되고 있다는 것이 업계 일반론이다."
        ),
    },
    {
        "title": "가격 결정력",
        "body": (
            "3사 모두 공급 조절을 통해 가격에 영향을 미칠 수 있지만, 개별 기업이 "
            "가격을 일방적으로 통제하지는 못한다 — 사이클성 공급 초과/부족이 "
            "가격의 주된 변동 요인이다(이 프로젝트의 4개 시나리오 확률가중치가 "
            "다루는 것이 이 변동성이다)."
        ),
    },
]


def _get_micron_bps(db: Session, as_of: date) -> tuple[float, int, int] | None:
    """(BPS, fiscal_year, fiscal_quarter)를 반환한다. 데이터 없으면 None."""
    asset = db.query(DimAsset).filter_by(code=MICRON_SYMBOL).first()
    if asset is None:
        return None
    row = (
        visible_as_of(db.query(FactFinancialQuarterly), FactFinancialQuarterly, as_of)
        .filter_by(asset_id=asset.asset_id)
        .filter(FactFinancialQuarterly.bps.isnot(None))
        .order_by(FactFinancialQuarterly.fiscal_year.desc(), FactFinancialQuarterly.fiscal_quarter.desc())
        .first()
    )
    if row is None or row.bps is None:
        return None
    return float(row.bps), row.fiscal_year, row.fiscal_quarter


def build_industry_context(db: Session, as_of: date, samsung: dict, hynix: dict) -> dict:
    """samsung/hynix는 build_valuation_context가 이미 계산한 company dict를 그대로 받는다
    (book_value/book_value_source가 이미 실측/폴백을 반영해 재계산하지 않는다)."""
    micron = _get_micron_bps(db, as_of)

    if micron is None:
        return {
            "industry_available": True,
            "industry_structure_cards": INDUSTRY_STRUCTURE_CARDS,
            "industry_micron_available": False,
            "industry_micron_data_status": (
                "마이크론 BPS 데이터 없음 — ingest_micron_financials.py 미실행 또는 "
                "FMP 응답 없음"
            ),
        }

    micron_bps_usd, fiscal_year, fiscal_quarter = micron
    return {
        "industry_available": True,
        "industry_structure_cards": INDUSTRY_STRUCTURE_CARDS,
        "industry_micron_available": True,
        "industry_bps_rows": [
            ["삼성전자", f"{samsung['book_value']:,.0f}원", samsung["book_value_source"]],
            ["SK하이닉스", f"{hynix['book_value']:,.0f}원", hynix["book_value_source"]],
            ["마이크론", f"${micron_bps_usd:,.2f}", f"FMP {fiscal_year} Q{fiscal_quarter} 실측 BPS"],
        ],
        "industry_bps_disclosure": (
            "장부가치는 통화가 서로 달라(원화 vs 미국 달러) 절대금액으로 직접 "
            "비교할 수 없다 — 이 표는 각 기업의 데이터 출처가 실측인지를 "
            "확인하는 용도이지, 3사 밸류에이션을 비교하는 표가 아니다. 마이크론에 "
            "대해서는 이 프로젝트가 RIM 적정가를 계산하지 않는다(시나리오·"
            "자기자본비용 가정이 삼성전자·SK하이닉스에만 설계돼 있음)."
        ),
    }
