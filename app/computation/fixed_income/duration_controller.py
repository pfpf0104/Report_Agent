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
        city_ai = synthetic_city_ai_output(origin)
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

    return {
        "title": f"{as_of.month}월 예비 운용안",
        "subtitle": "carry-price gate와 고정 tanh 경고로 3년 목표 듀레이션을 매월 재계산한다",
        "meta_lines": [f"SHADOW 점검 {as_of.isoformat()}", "월말형 SHADOW · Convention C", "한국 채권지수 슬리브"],
        "headline": headline,
        "headline_body": headline_body,
        "cards": cards,
        "ledger_rows": ledger_rows,
        "source": "MetroGuard-KR · 월말 운용·연구 보고서 (City AI 입력은 합성 데이터)",
    }
