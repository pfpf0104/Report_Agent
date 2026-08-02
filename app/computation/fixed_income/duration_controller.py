"""MetroGuard-KR bounded controller: carry-price gate → tanh 경고 → 목표 듀레이션.

방법론 (첨부 MetroGuard-KR 보고서 8페이지 "경고를 주문으로 바꾸는 법" 기준):

  1단계 · carry-price gate (H=63거래일, D_long=3년, D_short=1년):
    A⁻_t = (D_long-D_short)·q̂_t − (Y_t(D_long)-Y_t(D_short))·H/252   [bp]
    앞 항은 단축(D3→D1)으로 피할 것으로 예상되는 가격손실, 뒤 항은 같은
    기간 포기하는 carry다. (원문은 q̂·Y를 퍼센트포인트 단위로 두고 전체에
    ×100을 곱하지만, q̂·Y를 bp로 그대로 쓰면 ×100 없이 같은 값이 나온다 —
    지면의 숫자 예시 A⁻=27.5bp로 검산 완료.)

  2단계 · 고정 규칙 tanh 경고(학습되는 threshold 없음):
    g_t = 1(q̂_t>0) × max(0, mean_s[tanh(A⁻_t/s)]),  s ∈ {2.5,5,10,20}bp
    q̂_t≤0 또는 A⁻_t≤0이면 g_t=0. (지면 예시 A⁻=27.5bp → g≈0.968로 검산 완료.)

  3단계 · 목표 듀레이션(활성 lot 동일가중 평균):
    각 g는 생성 후 H(63)거래일 동안 "활성 lot"으로 유지된다.
    D*_t = D_long − (D_long−D_short) × mean(활성 lot들의 g)
    (지면 예시 단일 lot g≈0.968 → D*≈1.06년으로 검산 완료.)

City AI의 예측 q̂_t와 실제 국채 1y/3y 금리는 이 컨트롤러의 입력일 뿐이며
city_ai_stub.py의 합성 데이터로 대신한다(그 파일의 TODO 참고). 이 파일의
알고리즘(gate/경고/lot ledger/목표 듀레이션)은 순수 함수·클래스라 실제
City AI 출력으로 교체해도 그대로 재사용된다.
"""
from __future__ import annotations

import calendar
from dataclasses import dataclass, field
from datetime import date

import numpy as np
from sqlalchemy.orm import Session

from app.computation.fixed_income.city_ai_stub import synthetic_city_ai_output
from app.computation.risk.report_context import build_duration_performance_context

D_LONG_YEARS = 3.0
D_SHORT_YEARS = 1.0
HORIZON_TRADING_DAYS = 63
TANH_SCALES_BP = (2.5, 5.0, 10.0, 20.0)


@dataclass(frozen=True)
class CarryPriceGate:
    predicted_change_bp: float  # q̂_t
    curve_spread_bp: float  # Y_t(3) - Y_t(1)
    a_minus_bp: float  # A⁻_t


def compute_carry_price_gate(
    predicted_change_bp: float, yield_3y_bp: float, yield_1y_bp: float
) -> CarryPriceGate:
    spread_bp = yield_3y_bp - yield_1y_bp
    a_minus_bp = (D_LONG_YEARS - D_SHORT_YEARS) * predicted_change_bp - spread_bp * (
        HORIZON_TRADING_DAYS / 252
    )
    return CarryPriceGate(predicted_change_bp, spread_bp, a_minus_bp)


def compute_warning(gate: CarryPriceGate) -> float:
    """q̂_t≤0 또는 A⁻_t≤0이면 0. 아니면 4개 tanh 척도의 평균(0 이상으로 클램프)."""
    if gate.predicted_change_bp <= 0 or gate.a_minus_bp <= 0:
        return 0.0
    tanh_values = [np.tanh(gate.a_minus_bp / s) for s in TANH_SCALES_BP]
    return max(0.0, float(np.mean(tanh_values)))


def _trading_days_between(start: date, end: date) -> int:
    """평일 수 근사(공휴일 미반영) — 실제로는 한국 거래소 캘린더가 필요하다."""
    if end <= start:
        return 0
    return int(np.busday_count(start, end))


@dataclass
class DurationLot:
    origin_date: date
    g: float


@dataclass
class LotLedger:
    """월말마다 새 lot을 추가하고, 생성 후 63거래일이 지나면 활성에서 빠진다."""

    lots: list[DurationLot] = field(default_factory=list)

    def add_lot(self, origin_date: date, g: float) -> None:
        self.lots.append(DurationLot(origin_date, g))

    def active_lots(self, as_of: date) -> list[DurationLot]:
        return [
            lot
            for lot in self.lots
            if lot.origin_date <= as_of
            and _trading_days_between(lot.origin_date, as_of) < HORIZON_TRADING_DAYS
        ]


def compute_target_duration(ledger: LotLedger, as_of: date) -> float | None:
    """활성 lot이 하나도 없으면 None(방향 없음 = 기존 듀레이션 유지)."""
    active = ledger.active_lots(as_of)
    if not active:
        return None
    mean_g = float(np.mean([lot.g for lot in active]))
    return D_LONG_YEARS - (D_LONG_YEARS - D_SHORT_YEARS) * mean_g


def _month_end(year: int, month: int) -> date:
    return date(year, month, calendar.monthrange(year, month)[1])


def _trailing_month_ends(as_of: date, count: int) -> list[date]:
    """as_of가 속한 달을 포함해 과거 count개월의 월말 origin 날짜를 만든다.

    실제로는 매월 워크포워드로 DB에 쌓인 lot을 그대로 조회하면 되지만,
    지금은 origin_date별 City AI 출력이 영속화돼 있지 않으므로 매 호출마다
    최근 몇 달치를 재구성해 활성 lot 집합을 채운다. HORIZON_TRADING_DAYS(63
    거래일 ≈ 3개월)보다 넉넉하게 4개월을 되짚어 현재 활성 lot을 놓치지 않는다.
    """
    origins = []
    year, month = as_of.year, as_of.month
    for _ in range(count):
        origins.append(_month_end(year, month))
        month -= 1
        if month == 0:
            month = 12
            year -= 1
    return sorted(o for o in origins if o <= as_of)


def build_metroguard_context(db: Session, as_of: date) -> dict:
    ledger = LotLedger()
    decisions = []
    for origin in _trailing_month_ends(as_of, count=4):
        city_ai = synthetic_city_ai_output(db, origin)
        gate = compute_carry_price_gate(
            city_ai["predicted_change_bp"], city_ai["yield_3y_bp"], city_ai["yield_1y_bp"]
        )
        g = compute_warning(gate)
        ledger.add_lot(origin, g)
        decisions.append({"origin": origin, "gate": gate, "g": g})

    d_star = compute_target_duration(ledger, as_of)
    active = ledger.active_lots(as_of)
    latest = decisions[-1]

    if d_star is None:
        headline = "활성 lot 없음 · 방향 없음"
        headline_body = "63거래일 이내 생성된 lot이 없어 목표 듀레이션을 새로 계산하지 않는다. 기존 듀레이션을 유지한다."
        tone = None
    elif latest["g"] > 0:
        headline = f"단축 경고 ON · D*={d_star:.2f}년"
        headline_body = (
            f"최근 origin({latest['origin'].isoformat()})의 carry-price gate A⁻={latest['gate'].a_minus_bp:.2f}bp, "
            f"신규 경고 g={latest['g']:.3f}. 활성 lot {len(active)}개 평균으로 목표 듀레이션을 {d_star:.2f}년으로 낮춘다."
        )
        tone = "down"
    else:
        headline = f"단축 경고 OFF · D*={d_star:.2f}년"
        headline_body = (
            f"최근 origin({latest['origin'].isoformat()})은 신규 경고가 없다(g=0). "
            f"활성 lot {len(active)}개 평균으로 목표 듀레이션은 {d_star:.2f}년."
        )
        tone = None

    cards = [
        {
            "label": "목표 듀레이션 D*",
            "value": f"{d_star:.2f}년" if d_star is not None else "N/A",
            "caption": f"활성 lot {len(active)}개 동일가중 평균",
            "tone": tone,
        },
        {
            "label": "최근 carry-price gate A⁻",
            "value": f"{latest['gate'].a_minus_bp:+.2f}bp",
            "caption": f"origin {latest['origin'].isoformat()}",
            "tone": "up" if latest["gate"].a_minus_bp > 0 else "down",
        },
        {
            "label": "최근 신규 경고 g",
            "value": f"{latest['g']:.3f}",
            "caption": f"q̂={latest['gate'].predicted_change_bp:+.1f}bp",
            "tone": "up" if latest["g"] > 0 else None,
        },
    ]

    ledger_rows = [
        [
            d["origin"].isoformat(),
            f"{d['gate'].predicted_change_bp:+.1f}",
            f"{d['gate'].a_minus_bp:+.2f}",
            f"{d['g']:.3f}",
            "활성" if d["origin"] in {lot.origin_date for lot in active} else "만료",
        ]
        for d in decisions
    ]

    index_weights = _index_weight_split(d_star) if d_star is not None else None
    sensitivity_rows = _q_hat_sensitivity_rows(active, latest["gate"].curve_spread_bp)

    return {
        "title": f"{as_of.month}월 예비 운용안",
        "subtitle": "carry-price gate와 고정 tanh 경고로 3년 목표 듀레이션을 매월 재계산한다",
        "meta_lines": [f"SHADOW 점검 {as_of.isoformat()}", "월말형 SHADOW · Convention C", "한국 채권지수 슬리브"],
        "headline": headline,
        "headline_body": headline_body,
        "cards": cards,
        "ledger_rows": ledger_rows,
        "index_weight_chart_uri": _index_weight_chart(index_weights) if index_weights else None,
        "formula_cards": FORMULA_CARDS,
        "workflow_steps": WORKFLOW_STEPS,
        "checklist_items": CHECKLIST_ITEMS,
        **build_duration_performance_context(db, as_of),
        "sensitivity_rows": sensitivity_rows,
        "warning_function_chart_uri": _warning_function_chart(),
        "historical_g_chart_uri": _historical_g_chart(db, as_of),
        "glossary_cards": GLOSSARY_CARDS,
        "source": "MetroGuard-KR · 월말 운용·연구 보고서 (금리커브는 실측, 63거래일 예측은 합성 데이터)",
    }


def _q_hat_sensitivity_rows(
    active_lots: list[DurationLot], curve_spread_bp: float, deltas_bp: tuple[float, ...] = (-20.0, -10.0, 0.0, 10.0, 20.0)
) -> list[list[str]]:
    """다음 origin에서 City AI q̂ 예측치가 delta였다면 새 lot의 g와 결합 D*가 어떻게
    바뀌는지 본다. 현재 활성 lot들의 g는 그대로 두고 신규 lot 하나만 가정한다.
    커브 스프레드는 가장 최근 관측값으로 고정한다(현재 활성 lot들의 값은 관측된
    실제 origin의 스프레드를 이미 반영하고 있어 재계산 대상이 아니다).
    """
    active_g = [lot.g for lot in active_lots]
    rows = []
    for delta in deltas_bp:
        gate = compute_carry_price_gate(delta, curve_spread_bp, 0.0)
        g = compute_warning(gate)
        combined_g = active_g + [g]
        d_star = D_LONG_YEARS - (D_LONG_YEARS - D_SHORT_YEARS) * float(np.mean(combined_g))
        rows.append([f"{delta:+.0f}bp", f"{g:.3f}", f"{d_star:.2f}년"])
    return rows


def _warning_function_chart() -> str:
    """g = mean_s[tanh(A⁻/s)]의 실제 모양을 A⁻ 0~100bp 구간에서 그린다(합성 아님,
    compute_warning을 그대로 호출). q̂=1bp(양수)로 고정해 부호 게이트만 통과시킨다.
    """
    from app.rendering.chart_service import line_chart

    a_minus_range = list(range(0, 105, 5))
    x_labels = [f"{a}" for a in a_minus_range]
    g_values = [compute_warning(CarryPriceGate(1.0, 0.0, float(a))) for a in a_minus_range]
    return line_chart(x_labels, {"g (경고 강도)": g_values}, figsize=(6.2, 2.2), max_x_ticks=6)


def _historical_g_chart(db: Session, as_of: date, months: int = 12) -> str:
    """운용에 쓰이는 활성 lot 원장(최근 4개월)보다 긴 12개월 g 추이를 참고용으로
    그린다. 같은 실제 함수(compute_carry_price_gate/compute_warning)를 더 긴
    구간에 적용한 것으로, lot 활성 여부 판정과는 별개다.
    """
    from app.rendering.chart_service import line_chart

    origins = _trailing_month_ends(as_of, count=months)
    g_values = []
    for origin in origins:
        city_ai = synthetic_city_ai_output(db, origin)
        gate = compute_carry_price_gate(
            city_ai["predicted_change_bp"], city_ai["yield_3y_bp"], city_ai["yield_1y_bp"]
        )
        g_values.append(compute_warning(gate))
    x_labels = [o.strftime("%Y-%m") for o in origins]
    return line_chart(x_labels, {"g (경고 강도)": g_values}, figsize=(6.2, 2.2), max_x_ticks=6)


GLOSSARY_CARDS = [
    {"title": "q̂ (predicted change)", "body": "City AI가 예측한 향후 63거래일간 한국 3년 국채 금리 변화(bp). 양수면 금리 상승 예측이다."},
    {"title": "A⁻ (carry-price gate)", "body": "3년→1년 단축 시 예상 가격방어분에서 포기하는 carry를 뺀 값(bp). 양수여야 단축을 검토할 근거가 된다."},
    {"title": "g (경고 강도)", "body": "A⁻를 4개 tanh 척도로 평균한 0~1 사이 값. g=0이면 신규 방어 lot을 열지 않는다."},
    {"title": "D* (목표 듀레이션)", "body": "63거래일 내 생성된 활성 lot들의 g를 동일가중 평균해 1~3년 사이로 환산한 목표 듀레이션."},
    {"title": "lot / 활성 lot", "body": "매월 말 생성되는 경고 기록 단위. 생성 후 63거래일(약 3개월) 동안만 목표 듀레이션 계산에 포함된다."},
]


def _index_weight_split(d_star: float) -> dict[str, float]:
    """목표 듀레이션 D*를 인접한 두 지수(1-3년물, 3-5년물) 비중으로 표시한다.

    첨부 보고서의 정확한 산출식은 공개되어 있지 않다 — 여기서는 D*가 두 지수의
    근사 듀레이션 사이에서 선형으로 위치한다고 가정한 근사치다. 앵커는 반드시
    이 컨트롤러가 실제로 낼 수 있는 D* 범위(D_SHORT_YEARS~D_LONG_YEARS, 즉
    1~3년)와 일치시킨다 — 그렇지 않으면 D*가 앵커 구간 밖에 clip되어 비중이
    항상 100/0으로 굳어버리는 문제가 생긴다(2.5~4.0년을 앵커로 썼을 때 실제로
    이 현상이 발생함을 확인하고 수정함).
    """
    short_duration, long_duration = D_SHORT_YEARS, D_LONG_YEARS
    weight_long = (d_star - short_duration) / (long_duration - short_duration)
    weight_long = min(max(weight_long, 0.0), 1.0)
    return {"1-3년 국채지수": (1 - weight_long) * 100, "3-5년 국채지수": weight_long * 100}


def _index_weight_chart(weights: dict[str, float]) -> str:
    from app.rendering.chart_service import horizontal_bar_chart

    return horizontal_bar_chart(list(weights.keys()), list(weights.values()), figsize=(6.2, 1.3))


# 방법론 설명(월별로 바뀌지 않는 고정 콘텐츠) — 첨부 보고서 6·8페이지 기준.
FORMULA_CARDS = [
    {"title": "01 · PRICE — 금리가 오르면 짧을수록 덜 잃는다", "body": "D3를 D1로 낮추면 금리상승에 대한 가격 민감도가 약 2년 줄어든다. 40bp 상승이면 carry·convexity 전 가격효과의 차이는 대략 80bp다."},
    {"title": "02 · CARRY — 단축의 기회비용도 먼저 계산한다", "body": "단기 슬리브의 yield가 낮으면 carry를 포기한다. MetroGuard는 예상 가격방어가 이 carry 회복분을 넘을 때만 신규 방어경고를 허용한다."},
    {"title": "03 · AUTHORITY — AI의 자본권한은 단축으로 제한한다", "body": "목표 듀레이션은 1~3년입니다. 금리하락 예측에 신규 단축을 만들지 않지만, 3년 위로 연장하는 권한도 주지 않는다."},
]

WORKFLOW_STEPS = [
    {"title": "정보 동결", "body": "월말까지 공개된 한국·미국 금리와 전국·도시 주택을 같은 시점표에 묶는다."},
    {"title": "라벨 성숙", "body": "한국 3년 금리의 63거래일 결과가 확정되고 7일 embargo가 지난 행만 남긴다."},
    {"title": "PCA-Ridge 학습", "body": "학습창 안에서만 정규표준화하고 PCA 8개와 Ridge로 3년 금리변화를 예측한다."},
    {"title": "상승위험 경고", "body": "예상 가격방어가 단기회로 포기하는 carry를 이길 때만 신규 방어 lot을 연다."},
    {"title": "목표 듀레이션", "body": "63거래일 동안 유효한 활성 lot을 평균해 1~3년의 일방향 목표를 계산한다."},
    {"title": "다음 종가 집행", "body": "전일 17시 marks로 인접 지수 비중을 정하고 다음 종가에 연동 0.5~1bp를 차감한다."},
]

CHECKLIST_ITEMS = [
    "주택 공개본 동결 — 새 월까지 실제 공개된 도시 단면과 공개시점을 저장한다.",
    "한국 거래일 정렬 — 17시 정보확정과 다음 적격 종가를 별도 사건으로 기록한다.",
    "60개월 재학습 — 63거래일 라벨과 7일 embargo가 끝난 행만 학습창에 넣는다.",
    "경고·목표 고정 — 수익을 보기 전에 경고, 활성 lot, 목표 듀레이션을 원장에 남긴다.",
    "비용·추적 확인 — 만기별 편도 0.5~1bp와 다음 종가 듀레이션 오차를 검사한다.",
    "정본 승격 — 49번째 미확정 결과는 성숙 후에만 평가하고 새 forward 결과와 구분한다.",
]
