"""시나리오 확률가중치 근거 문서화 — MASTER_PLAN Phase 4-5.

4개 시나리오(제한적/점진적/공격적 추격, 가격전쟁)의 확률가중치 20/50/25/5%는
기존에도 코드 곳곳에 있었지만(CYCLE_SCENARIO_CARDS의 과거 사이클 대응, "과거
사이클 사례는 정성적 참고이며 확률가중치를 기계적으로 도출하지 않았다"는 푸터
공시), "왜 이 숫자인가"를 한 곳에 모아 명시적으로 서술하는 페이지는 없었다.

## 이 모듈이 하지 않는 것

확률가중치를 통계적으로 재도출하지 않는다 — 과거 D램 사이클의 실제 확률분포를
추정하려면 여러 사이클에 걸친 정량 데이터(공급증가율·재고지표 등)가 필요한데
이 프로젝트에는 그런 시계열이 없다. 대신 두 가지만 한다:

1. **정성적 근거를 구조화한다.** 각 시나리오가 어떤 과거 국면에 대응하는지,
   왜 그 확률을 배정했는지를 명시적으로 서술한다(추측이 아니라 이미
   CYCLE_SCENARIO_CARDS에 있던 서술을 재사용·확장).
2. **확률가중치 자체의 민감도를 계산한다.** "중심 시나리오 확률이 50%가
   아니라 40%/60%였다면 최종 적정가가 얼마나 바뀌는가"는 순수 계산이라
   검증 가능하다 — cost_of_equity_sensitivity와 같은 성격의 민감도 분석.
"""
from __future__ import annotations

from dataclasses import replace

from app.computation.valuation.residual_income_model import RimScenario, compute_rim_value

# 시나리오별 확률가중치 배정 근거 — 이미 CYCLE_SCENARIO_CARDS에 있던 과거
# 사이클 대응 서술에, "왜 이 확률 수준인가"를 한 문장씩 덧붙인다.
SCENARIO_PROBABILITY_RATIONALE = [
    {
        "scenario": "제한적 추격",
        "weight": 0.20,
        "historical_analog": "2016~2017년 슈퍼사이클 — 공급이 타이트하게 유지된 국면",
        "why_this_weight": (
            "미세공정 전환 지연으로 격차가 유지되는 국면은 반복돼 왔지만, 지금은 "
            "후발주자의 자본투자 규모가 과거보다 커서 같은 강도로 재현될 확률을 "
            "중심 시나리오보다 낮게(20%) 잡는다."
        ),
    },
    {
        "scenario": "점진적 추격",
        "weight": 0.50,
        "historical_analog": "메모리 사이클의 '평상시' 국면 — 특정 단일 사이클보다 장기 평균적 패턴",
        "why_this_weight": (
            "완만한 점유율 이전과 ROE 정상화는 특정 위기·붐 국면이 아니라 사이클의 "
            "기본 상태에 가깝다고 보아, 4개 시나리오 중 가장 높은 확률(50%)을 "
            "중심 시나리오로 배정한다 — 나머지 세 시나리오는 이 중심에서 벗어나는 "
            "꼬리로 취급한다."
        ),
    },
    {
        "scenario": "공격적 추격",
        "weight": 0.25,
        "historical_analog": "2018~2019년 D램 다운사이클 — 대규모 증설로 가격이 빠르게 정상화된 국면",
        "why_this_weight": (
            "공급 과잉발 다운사이클은 반복적으로 발생해온 패턴이라 제한적 추격보다 "
            "높은 확률(25%)을 배정하지만, 여전히 중심 시나리오보다는 낮게 둔다."
        ),
    },
    {
        "scenario": "가격전쟁",
        "weight": 0.05,
        "historical_analog": "2011년 D램 치킨게임 — 저가 출혈 경쟁으로 ROE가 자기자본비용을 밑돈 국면",
        "why_this_weight": (
            "발생 빈도 자체는 낮지만(과거 10여 년간 1회 수준) 영향이 크므로 완전히 "
            "배제하지 않고 꼬리위험으로 낮은 확률(5%)을 명시적으로 남긴다 — 확률을 "
            "0으로 두면 꼬리위험이 적정가 계산에서 사라진다."
        ),
    },
]


def probability_weight_sensitivity(
    book_value_0: float,
    scenarios: list[RimScenario],
    *,
    base_case_name: str = "점진적 추격",
    tail_case_name: str = "가격전쟁",
    shift_pct_pt: float = 10.0,
) -> list[dict]:
    """중심 시나리오(base_case) 확률을 ±shift_pct_pt만큼 흔들고, 그만큼을
    꼬리 시나리오(tail_case)에서 빼거나 더해 최종 적정가가 얼마나 바뀌는지 본다.

    다른 시나리오의 가중치는 그대로 두므로 shift는 base_case와 tail_case
    사이에서만 이동한다 — 4개 가중치 합이 항상 1.0으로 유지된다.
    """
    base_value = sum(sc.weight * compute_rim_value(book_value_0, sc) for sc in scenarios)

    rows = []
    for shift in (-shift_pct_pt, 0.0, shift_pct_pt):
        shifted_scenarios = []
        for sc in scenarios:
            if sc.name == base_case_name:
                shifted_scenarios.append(replace(sc, weight=sc.weight + shift / 100))
            elif sc.name == tail_case_name:
                shifted_scenarios.append(replace(sc, weight=sc.weight - shift / 100))
            else:
                shifted_scenarios.append(sc)

        value = sum(sc.weight * compute_rim_value(book_value_0, sc) for sc in shifted_scenarios)
        base_case_weight = next(sc.weight for sc in shifted_scenarios if sc.name == base_case_name)
        rows.append(
            {
                "base_case_weight_pct": base_case_weight * 100,
                "value": value,
                "change_pct": (value / base_value - 1) * 100,
            }
        )
    return rows


def build_scenario_rationale_context(
    samsung: dict, hynix: dict, samsung_scenarios: list[RimScenario], hynix_scenarios: list[RimScenario]
) -> dict:
    """samsung/hynix는 build_valuation_context가 이미 계산한 company dict를
    그대로 받는다(book_value가 이미 실측/폴백을 반영했으므로 재계산하지 않는다).
    시나리오 리스트는 residual_income_model.py의 SAMSUNG_SCENARIOS/SK_HYNIX_SCENARIOS를
    호출부가 전달한다(이 모듈이 그 상수를 직접 임포트하면 순환 임포트가 된다 —
    residual_income_model.py가 이미 이 모듈을 임포트하고 있어서)."""
    samsung_sensitivity = probability_weight_sensitivity(samsung["book_value"], samsung_scenarios)
    hynix_sensitivity = probability_weight_sensitivity(hynix["book_value"], hynix_scenarios)

    return {
        "scenario_rationale_available": True,
        "scenario_rationale_rows": SCENARIO_PROBABILITY_RATIONALE,
        "scenario_rationale_disclosure": (
            "이 확률가중치는 과거 D램 사이클 국면에 대한 정성적 대응이며, 여러 "
            "사이클에 걸친 정량 데이터로 통계적으로 추정한 값이 아니다. 아래 "
            "민감도는 이 확률가중치 자체가 바뀌었을 때 적정가가 얼마나 움직이는지 "
            "보여줄 뿐, 확률가중치가 옳다는 근거가 아니다."
        ),
        "samsung_probability_sensitivity_rows": [
            [f"{r['base_case_weight_pct']:.0f}%", f"{r['value']:,.0f}원", f"{r['change_pct']:+.1f}%"]
            for r in samsung_sensitivity
        ],
        "hynix_probability_sensitivity_rows": [
            [f"{r['base_case_weight_pct']:.0f}%", f"{r['value']:,.0f}원", f"{r['change_pct']:+.1f}%"]
            for r in hynix_sensitivity
        ],
    }
