"""삼성전자·SK하이닉스: 5년 전환형 잔여이익모형(RIM) + 리포트 context 빌더.

방법론 (첨부 밸류에이션 보고서 3·5·11페이지 "5년 전환형 잔여이익모형" 기준):
  기업가치 = 현재 장부가치 + 향후 5년 초과이익의 현재가치 + 5년차 이후 영구성장 잔여가치
  초과이익_t = (ROE_t - r) × 장부가치_{t-1}
  장부가치_t = 장부가치_{t-1} × (1 + ROE_t × (1 - 총지급률_t))
  Terminal value = (정상상태 ROE - r) × 장부가치_5 / (r - g), 5년 할인해 현재가치화

4개 시나리오(제한적/점진적/공격적 추격, 가격전쟁)를 확률가중 평균해 최종 적정가를 낸다.

주: 보고서 5페이지는 자기자본비용을 "9.5~10.0%" 같은 범위로 표시하지만, 부록
A-2의 정상상태 r 단일값을 역산해보면 그 범위는 삼성전자(하한)와 SK하이닉스(상한)
값을 합쳐 표시한 것이었다 — 이 코드에서 그 값들로 두 기업의 적정가격을 원 단위로
재현해 검산했다(예: 삼성전자 점진적 추격 229,640원, SK하이닉스 제한적 추격
2,914,632원 등, 오차 수십 원 이내).

TODO(실데이터 연동): book_value_0(분석 기준 BPS)은 지금 보고서 5페이지의 값을
그대로 고정했다. DART 연동 후에는 fact_financial_quarterly에서 최신 분기
BPS/EPS를 조회해 대체해야 한다.

현재가는 fact_market_daily에 KIS 데이터가 있으면 그걸 쓰고(ingest_korean_
equity_prices.py), 없으면 보고서 고정값으로 폴백한다 — 이 세션은 네트워크가
막혀 있어 KIS 실데이터가 실제로 채워진 상태를 검증하지 못했으므로 폴백 경로가
항상 실행된다.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from sqlalchemy.orm import Session

from app.db.models.dim_asset import DimAsset
from app.db.models.fact_market_daily import FactMarketDaily


@dataclass(frozen=True)
class RimScenario:
    name: str
    weight: float
    roe_path: tuple[float, ...]  # 1~5년차 ROE, %
    payout_path: tuple[float, ...]  # 1~5년차 총지급률, %
    cost_of_equity: float  # r, %
    terminal_roe: float  # 정상상태 ROE, %
    terminal_growth: float  # 정상상태 장기 g, %


def compute_rim_value(book_value_0: float, scenario: RimScenario) -> float:
    """5년 명시적 예측 + Gordon growth terminal value로 기업가치를 계산한다."""
    r = scenario.cost_of_equity / 100
    bv = book_value_0
    pv_sum = 0.0
    for t, (roe_pct, payout_pct) in enumerate(zip(scenario.roe_path, scenario.payout_path), start=1):
        roe = roe_pct / 100
        payout = payout_pct / 100
        net_income = roe * bv
        excess_income = net_income - r * bv
        pv_sum += excess_income / (1 + r) ** t
        bv = bv * (1 + roe * (1 - payout))

    terminal_excess = (scenario.terminal_roe / 100 - r) * bv
    g = scenario.terminal_growth / 100
    terminal_value = terminal_excess / (r - g)
    pv_terminal = terminal_value / (1 + r) ** len(scenario.roe_path)

    return book_value_0 + pv_sum + pv_terminal


def probability_weighted_value(book_value_0: float, scenarios: list[RimScenario]) -> dict:
    rows = []
    weighted_sum = 0.0
    for sc in scenarios:
        value = compute_rim_value(book_value_0, sc)
        weighted_sum += sc.weight * value
        rows.append({"scenario": sc.name, "weight": sc.weight, "value": value})
    return {"rows": rows, "weighted_value": weighted_sum}


# 첨부 보고서 5·11페이지 값 그대로. r은 위 docstring 설명대로 삼성=하한/SK하이닉스=상한.
SAMSUNG_BOOK_VALUE = 81_500.0
SAMSUNG_SCENARIOS = [
    RimScenario("제한적 추격", 0.20, (65, 45, 30, 22, 18), (15, 20, 30, 45, 78.1), 9.5, 16.0, 3.5),
    RimScenario("점진적 추격", 0.50, (58, 36, 24, 17, 14.5), (15, 20, 35, 50, 77.8), 10.2, 13.5, 3.0),
    RimScenario("공격적 추격", 0.25, (50, 28, 17, 12, 10.5), (15, 25, 45, 60, 76.2), 11.0, 10.5, 2.5),
    RimScenario("가격전쟁", 0.05, (45, 22, 12, 9, 9), (15, 30, 55, 70, 77.8), 11.5, 9.0, 2.0),
]

SK_HYNIX_BOOK_VALUE = 364_000.0
SK_HYNIX_SCENARIOS = [
    RimScenario("제한적 추격", 0.20, (95, 60, 38, 27, 22), (10, 15, 25, 40, 82.5), 10.0, 20.0, 3.5),
    RimScenario("점진적 추격", 0.50, (80, 48, 30, 20, 17), (10, 15, 30, 50, 81.2), 10.5, 16.0, 3.0),
    RimScenario("공격적 추격", 0.25, (65, 35, 20, 14, 11.5), (10, 20, 40, 50, 78.3), 11.5, 11.5, 2.5),
    RimScenario("가격전쟁", 0.05, (55, 27, 14, 10, 9.5), (10, 25, 50, 70, 78.9), 12.0, 9.5, 2.0),
]

CURRENT_PRICE_FALLBACK = {"삼성전자": 208_500.0, "SK하이닉스": 1_401_000.0}
STOCK_CODE = {"삼성전자": "005930", "SK하이닉스": "000660"}


def _latest_close_price(db: Session, stock_code: str) -> float | None:
    asset = db.query(DimAsset).filter_by(code=stock_code).first()
    if asset is None:
        return None
    row = (
        db.query(FactMarketDaily)
        .filter_by(asset_id=asset.asset_id)
        .order_by(FactMarketDaily.trade_date.desc())
        .first()
    )
    if row is None or row.close is None:
        return None
    return float(row.close)


def _resolve_current_price(db: Session, name: str) -> tuple[float, str]:
    """KIS 실데이터가 fact_market_daily에 있으면 그걸, 없으면 보고서 고정값을 쓴다."""
    price = _latest_close_price(db, STOCK_CODE[name])
    if price is not None:
        return price, "KIS 실시간 시세"
    return CURRENT_PRICE_FALLBACK[name], "보고서 고정값(KIS 데이터 없음)"


def _company_row(db: Session, name: str, book_value: float, scenarios: list[RimScenario]) -> dict:
    result = probability_weighted_value(book_value, scenarios)
    current, price_source = _resolve_current_price(db, name)
    fair_value = result["weighted_value"]
    upside_pct = (fair_value / current - 1) * 100
    return {
        "name": name,
        "current_price": current,
        "price_source": price_source,
        "fair_value": fair_value,
        "upside_pct": upside_pct,
        "scenario_rows": result["rows"],
    }


def build_valuation_context(db: Session, as_of: date) -> dict:
    samsung = _company_row(db, "삼성전자", SAMSUNG_BOOK_VALUE, SAMSUNG_SCENARIOS)
    hynix = _company_row(db, "SK하이닉스", SK_HYNIX_BOOK_VALUE, SK_HYNIX_SCENARIOS)

    cards = [
        {
            "label": f"{c['name']} 확률가중 적정가",
            "value": f"{c['fair_value']:,.0f}원",
            "caption": f"현재가 {c['current_price']:,.0f}원({c['price_source']}) 대비 {c['upside_pct']:+.1f}%",
            "tone": "up" if c["upside_pct"] > 0 else "down",
        }
        for c in (samsung, hynix)
    ]

    table_rows = []
    for c in (samsung, hynix):
        for row in c["scenario_rows"]:
            change_pct = (row["value"] / c["current_price"] - 1) * 100
            table_rows.append(
                [
                    c["name"],
                    row["scenario"],
                    f"{row['weight']*100:.0f}%",
                    f"{row['value']:,.0f}원",
                    f"{change_pct:+.1f}%",
                ]
            )

    return {
        "title": "한국 대표 반도체 기업 적정가치 평가",
        "subtitle": "5년 전환형 잔여이익모형(RIM) — 시나리오별 ROE 경로·자기자본비용·장기 g로 확률가중 평균",
        "meta_lines": [f"보고서 기준일 {as_of.isoformat()}", "5년 명시적 예측 + Gordon growth terminal value"],
        "cards": cards,
        "table": {
            "columns": ["기업", "시나리오", "가중치", "적정가격", "현재가 대비"],
            "rows": table_rows,
        },
        "source": "독립 투자분석 보고서 (분석 기준 BPS는 보고서 고정값, DART 연동 전)",
    }
