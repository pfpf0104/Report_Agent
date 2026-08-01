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

from dataclasses import dataclass, replace
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


def cost_of_equity_sensitivity(
    book_value_0: float, base_scenario: RimScenario, deltas_pct_pt: tuple[float, ...] = (-1.0, -0.5, 0.0, 0.5, 1.0)
) -> list[dict]:
    """base_scenario(최대 가중치 시나리오)의 자기자본비용을 ±delta만큼 흔들어 적정가 민감도를 본다."""
    base_value = compute_rim_value(book_value_0, base_scenario)
    rows = []
    for delta in deltas_pct_pt:
        shifted = replace(base_scenario, cost_of_equity=base_scenario.cost_of_equity + delta)
        value = compute_rim_value(book_value_0, shifted)
        rows.append(
            {
                "cost_of_equity": shifted.cost_of_equity,
                "value": value,
                "change_pct": (value / base_value - 1) * 100,
            }
        )
    return rows


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

WHAT_AND_WHY_CARDS = [
    {
        "title": "01 · 장부가치가 출발점",
        "body": "반도체는 자본집약 산업이라 이익보다 장부가치(자기자본)가 먼저 확정된다. RIM은 이 장부가치에 ROE가 자기자본비용을 넘는 '초과이익'만 얹어 가치를 쌓는다.",
    },
    {
        "title": "02 · 사이클을 시나리오로 흡수",
        "body": "메모리는 호황·불황 진폭이 크다. 단일 실적 전망 대신 제한적/점진적/공격적 추격, 가격전쟁 4개 ROE 경로를 확률가중해 사이클 리스크를 명시적으로 반영한다.",
    },
    {
        "title": "03 · 배수(multiple) 가정을 쓰지 않는다",
        "body": "PER·PBR 같은 시장 배수는 그 자체가 밸류에이션 결론을 선반영한다. RIM은 ROE·자기자본비용·장기성장률만으로 적정가를 역산해 순환논리를 피한다.",
    },
]

FORMULA_CARDS = [
    {
        "title": "01 · 초과이익 (Excess Income)",
        "body": "초과이익_t = (ROE_t − r) × 장부가치_(t−1). ROE가 자기자본비용 r을 넘는 해에만 양(+)의 초과이익이 쌓인다.",
    },
    {
        "title": "02 · 장부가치 이월 (Book Value Roll-forward)",
        "body": "장부가치_t = 장부가치_(t−1) × (1 + ROE_t × (1 − 총지급률_t)). 배당·자사주로 지급되지 않고 유보된 이익만큼 다음 해 장부가치가 커진다.",
    },
    {
        "title": "03 · 잔여가치 (Terminal Value)",
        "body": "5년차 이후는 정상상태 ROE·장기성장률 g로 Gordon growth 잔여가치를 계산해 5년 할인한다. r>g가 유지되는 시나리오에서만 유한한 값이 나온다.",
    },
]

WORKFLOW_STEPS = [
    {"title": "최신 재무 확보", "body": "DART 최신 분기 BPS·ROE를 조회한다(연동 전에는 보고서 기준 고정값을 쓴다)."},
    {"title": "시나리오 설계", "body": "제한적/점진적/공격적 추격, 가격전쟁 4개 경로의 연도별 ROE·총지급률과 확률가중치를 정한다."},
    {"title": "자기자본비용 산정", "body": "시나리오별로 위험을 반영한 자기자본비용 r을 배정한다(공격적 시나리오일수록 r을 높게)."},
    {"title": "5년 명시적 현재가치화", "body": "연도별 초과이익을 계산해 r로 할인하고 장부가치를 다음 해로 이월한다."},
    {"title": "잔여가치 계산", "body": "정상상태 ROE·g로 5년차 이후 잔여가치를 구해 현재가치로 환산한다."},
    {"title": "확률가중 평균", "body": "장부가치+PV(초과이익)+PV(잔여가치)를 시나리오별로 구해 확률가중 평균한 값을 최종 적정가로 쓴다."},
]

CHECKLIST_ITEMS = [
    "BPS 최신화 — DART 연동 후 최신 분기 BPS로 book_value_0을 교체했는지 확인한다.",
    "자기자본비용 근거 — 무위험금리·베타·에퀴티리스크프리미엄 갱신분이 r에 반영됐는지 점검한다.",
    "시나리오 확률 재검토 — 실제 업황 전개가 특정 시나리오로 쏠리면 확률가중치를 재조정한다.",
    "r>g 성립 확인 — 잔여가치 계산의 분모(r−g)가 모든 시나리오에서 양수인지 검증한다.",
    "현재가 출처 — KIS 실시간 시세가 채워졌는지, 폴백 고정값을 쓰고 있지는 않은지 확인한다.",
]


def _scenario_bar_chart(company_row: dict) -> str:
    from app.rendering.chart_service import vertical_bar_chart

    labels = [row["scenario"] for row in company_row["scenario_rows"]]
    changes = [
        (row["value"] / company_row["current_price"] - 1) * 100 for row in company_row["scenario_rows"]
    ]
    return vertical_bar_chart(labels, changes, value_fmt="{:+.0f}%")


def _weight_donut_chart(scenarios: list[RimScenario]) -> str:
    from app.rendering.chart_service import donut_chart

    labels = [sc.name for sc in scenarios]
    values = [sc.weight * 100 for sc in scenarios]
    return donut_chart(labels, values, center_text="확률\n가중치")


def _comparison_bar_chart(samsung: dict, hynix: dict) -> str:
    from app.rendering.chart_service import vertical_bar_chart

    labels = [samsung["name"], hynix["name"]]
    values = [samsung["upside_pct"], hynix["upside_pct"]]
    return vertical_bar_chart(labels, values, figsize=(6.2, 2.2), value_fmt="{:+.1f}%")


def _roe_path_chart(scenarios: list[RimScenario]) -> str:
    """시나리오별 5개년 명시적 ROE 경로 — RimScenario.roe_path를 그대로 그린다(합성 아님)."""
    from app.rendering.chart_service import line_chart

    x_labels = [f"{t}년차" for t in range(1, len(scenarios[0].roe_path) + 1)]
    series = {sc.name: list(sc.roe_path) for sc in scenarios}
    return line_chart(x_labels, series, figsize=(6.2, 2.2), max_x_ticks=5)


def _sensitivity_range_text(sensitivity_rows: list[dict]) -> str:
    changes = [r["change_pct"] for r in sensitivity_rows]
    return f"{min(changes):+.1f}%~{max(changes):+.1f}%"


def _risk_cards(company: dict, scenarios: list[RimScenario]) -> list[dict]:
    """하드코딩된 리스크 서술이 아니라, 이미 계산된 시나리오·민감도 수치를 인용한다."""
    tail_weight = sum(sc.weight for sc in scenarios if sc.name in ("공격적 추격", "가격전쟁"))
    tail_value = next(r["value"] for r in company["scenario_rows"] if r["scenario"] == "가격전쟁")
    tail_change = (tail_value / company["current_price"] - 1) * 100
    sensitivity_range = _sensitivity_range_text(company["sensitivity_rows"])
    return [
        {
            "title": "사이클 하방 리스크",
            "body": f"공격적 추격+가격전쟁 결합 확률 {tail_weight*100:.0f}%. 가격전쟁 시나리오 적정가는 현재가 대비 {tail_change:+.1f}%.",
        },
        {
            "title": "자기자본비용 민감도",
            "body": f"기준 시나리오({company['base_scenario'].name})의 r을 ±1.0%p 흔들면 적정가는 {sensitivity_range} 구간에서 움직인다.",
        },
        {
            "title": "BPS 최신화 리스크",
            "body": f"현재 장부가치 {company['book_value']:,.0f}원은 DART 연동 전 보고서 고정값이다. 최신 분기 실적과 괴리가 있을 수 있다.",
        },
    ]


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
    base_scenario = max(scenarios, key=lambda sc: sc.weight)
    return {
        "name": name,
        "book_value": book_value,
        "current_price": current,
        "price_source": price_source,
        "fair_value": fair_value,
        "upside_pct": upside_pct,
        "scenario_rows": result["rows"],
        "base_scenario": base_scenario,
        "sensitivity_rows": cost_of_equity_sensitivity(book_value, base_scenario),
    }


def _scenario_rows_for(company: dict) -> list[list[str]]:
    rows = []
    for row in company["scenario_rows"]:
        change_pct = (row["value"] / company["current_price"] - 1) * 100
        rows.append(
            [
                row["scenario"],
                f"{row['weight']*100:.0f}%",
                f"{row['value']:,.0f}원",
                f"{change_pct:+.1f}%",
            ]
        )
    return rows


def _sensitivity_table_rows(company: dict) -> list[list[str]]:
    return [
        [f"{r['cost_of_equity']:.1f}%", f"{r['value']:,.0f}원", f"{r['change_pct']:+.1f}%"]
        for r in company["sensitivity_rows"]
    ]


def _assumption_rows(company: dict, scenarios: list[RimScenario]) -> list[list[str]]:
    return [
        [
            company["name"],
            sc.name,
            f"{sc.cost_of_equity:.1f}%",
            f"{sc.terminal_roe:.1f}%",
            f"{sc.terminal_growth:.1f}%",
        ]
        for sc in scenarios
    ]


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

    headline = "두 기업 모두 확률가중 적정가가 현재가를 상회" if samsung["upside_pct"] > 0 and hynix["upside_pct"] > 0 else "시나리오 간 괴리가 큰 구간 — 단일 목표가보다 분포로 해석"
    headline_body = (
        f"현재가 대비 확률가중 적정가 괴리는 삼성전자 {samsung['upside_pct']:+.1f}%, "
        f"SK하이닉스 {hynix['upside_pct']:+.1f}%. 현재가 출처 — 삼성전자: {samsung['price_source']}, "
        f"SK하이닉스: {hynix['price_source']}."
    )

    return {
        "title": "한국 대표 반도체 기업 적정가치 평가",
        "subtitle": "5년 전환형 잔여이익모형(RIM) — 시나리오별 ROE 경로·자기자본비용·장기 g로 확률가중 평균",
        "meta_lines": [f"보고서 기준일 {as_of.isoformat()}", "5년 명시적 예측 + Gordon growth terminal value"],
        "headline": headline,
        "headline_body": headline_body,
        "cards": cards,
        "comparison_chart_uri": _comparison_bar_chart(samsung, hynix),
        "table": {
            "columns": ["기업", "시나리오", "가중치", "적정가격", "현재가 대비"],
            "rows": table_rows,
        },
        "what_and_why_cards": WHAT_AND_WHY_CARDS,
        "formula_cards": FORMULA_CARDS,
        "workflow_steps": WORKFLOW_STEPS,
        "checklist_items": CHECKLIST_ITEMS,
        "samsung": {
            **samsung,
            "scenario_chart_uri": _scenario_bar_chart(samsung),
            "scenario_table_rows": _scenario_rows_for(samsung),
            "sensitivity_table_rows": _sensitivity_table_rows(samsung),
            "roe_chart_uri": _roe_path_chart(SAMSUNG_SCENARIOS),
            "risk_cards": _risk_cards(samsung, SAMSUNG_SCENARIOS),
        },
        "hynix": {
            **hynix,
            "scenario_chart_uri": _scenario_bar_chart(hynix),
            "scenario_table_rows": _scenario_rows_for(hynix),
            "sensitivity_table_rows": _sensitivity_table_rows(hynix),
            "roe_chart_uri": _roe_path_chart(SK_HYNIX_SCENARIOS),
            "risk_cards": _risk_cards(hynix, SK_HYNIX_SCENARIOS),
        },
        "weight_donut_chart_uri": _weight_donut_chart(SAMSUNG_SCENARIOS),
        "assumption_rows": _assumption_rows(samsung, SAMSUNG_SCENARIOS) + _assumption_rows(hynix, SK_HYNIX_SCENARIOS),
        "source": "독립 투자분석 보고서 (분석 기준 BPS는 보고서 고정값, DART 연동 전)",
    }
