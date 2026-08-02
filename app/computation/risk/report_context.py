"""실데이터 → 성과·리스크 페이지 컨텍스트.

Phase 0 백필로 5년 가격 이력이 생기면서, `performance_disclosure.py`가 비워 둔
성과 페이지를 실제 계산 결과로 채울 수 있게 됐다. 이 모듈이 그 연결부다.

## 무엇을 성과로 제시하는가 — 그리고 무엇을 제시하지 않는가

여기서 백테스트하는 것은 **위험 균등 배분(리스크패리티) 중립 포트폴리오**이지
CallRank 전략이 아니다. 이유는 하나다: CallRank의 섹터 점수는 아직
`sector_embeddings.py`의 난수에서 나온다(MASTER_PLAN G3). 그 점수로 비중을
기울인 백테스트 결과는 "난수를 성과처럼 제시"하는 G2와 정확히 같은 물건이 된다.

리스크패리티 중립 배분은 다르다. 규칙이 완전히 명시돼 있고(공분산 → 위험기여
균등), 입력이 실제 가격 이력뿐이며, 어떤 예측 신호도 들어가지 않는다. 따라서
이 결과는 "이 유니버스에 위험을 균등 배분했으면 어땠는가"라는 검증 가능한
사실이다. 리포트에는 그 성격을 명시해 CallRank 전략 성과로 오독되지 않게 한다.

신호가 실데이터로 교체되면(G3 해소) `weight_fn`에 tilt를 추가하는 것만으로
전략 백테스트로 확장된다 — 엔진과 컨텍스트는 그대로 쓴다.

## 점 추정을 그대로 믿지 않기

가설적(hypothetical) 백테스트는 실현 성과가 아니다. GIPS는 이 둘을 엄격히
구분하며, 이 모듈이 만드는 컨텍스트에는 항상 그 사실을 밝히는 문구가 들어간다.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import numpy as np
from sqlalchemy.orm import Session

from app.computation.backtest.engine import (
    WeightFn,
    from_covariance,
    periodic_rebalance_indices,
    run_backtest,
)
from app.computation.portfolio.constraints import ConstraintSet, relax_cap_to_feasible
from app.computation.portfolio.costs import CostModel
from app.computation.portfolio.weighting import risk_parity
from app.computation.risk import rolling
from app.computation.risk.gips import (
    build_gips_table,
    format_gips_table_rows,
    meets_gips_minimum_history,
)
from app.computation.risk.metrics import (
    TRADING_DAYS_PER_YEAR,
    annualized_return,
    annualized_volatility,
    beta,
    calmar_ratio,
    conditional_var,
    historical_var,
    information_ratio,
    max_drawdown,
    returns_from_prices,
    sharpe_ratio,
    sortino_ratio,
    tracking_error,
)
from app.db.models.dim_asset import DimAsset
from app.db.models.fact_market_daily import FactMarketDaily
from app.db.point_in_time import visible_as_of

# 공분산 추정 최소 관측치. portfolio/report_context.py와 같은 근거(1년)를 쓴다.
MIN_COVARIANCE_OBSERVATIONS = TRADING_DAYS_PER_YEAR

# 백테스트를 시도할 최소 총 관측치. 공분산 창(252) 이후 최소 1년은 성과 구간이
# 남아야 의미가 있으므로 2년으로 잡는다.
MIN_BACKTEST_OBSERVATIONS = TRADING_DAYS_PER_YEAR * 2

ROLLING_WINDOW = TRADING_DAYS_PER_YEAR  # 12개월 롤링
ROLLING_WINDOW_LABEL = f"12개월({TRADING_DAYS_PER_YEAR}거래일)"

DEFAULT_COST_MODEL = CostModel(spread_bps=5.0)
DEFAULT_CONSTRAINTS = ConstraintSet(max_weight=0.25)

# 아래 문구는 Jinja 템플릿에서 그대로 출력된다 — 마크다운 강조(**)를 쓰면
# 지면에 별표가 그대로 찍힌다(렌더링으로 확인). 평문으로 작성한다.
HYPOTHETICAL_DISCLOSURE = (
    "아래 수치는 과거 가격 이력에 규칙을 적용한 가설적(hypothetical) 백테스트 "
    "결과이며 실현된 운용 성과가 아니다. 실제 계좌에서 집행되지 않았고, 미래 "
    "성과를 시사하지 않는다. 거래비용은 아래 명시한 가정으로 차감했으며 세금·"
    "차입비용·현금 드래그는 반영하지 않았다."
)

NEUTRAL_STRATEGY_DISCLOSURE = (
    "백테스트 대상은 위험 균등 배분(리스크패리티) 중립 포트폴리오다. CallRank "
    "섹터 점수로 비중을 기울인 결과가 아니다 — 현재 점수는 실제 transcript 임베딩이 "
    "아니라 합성값이므로(MASTER_PLAN G3), 그 신호를 넣은 백테스트는 검증 가능한 "
    "근거를 갖지 못한다. 신호가 실데이터로 교체되면 동일한 엔진에 tilt만 추가한다."
)

FIXED_ALLOCATION_DISCLOSURE_TEMPLATE = (
    "백테스트 대상은 {label} 고정 배분이다. MetroGuard의 실제 목표 듀레이션(D*)은 "
    "City AI 예측(city_ai_stub.py)에 의존하는데, 이 예측은 아직 실제 PCA-Ridge "
    "모델이 아니라 합성 데이터다(MASTER_PLAN G4). 그 예측으로 매월 조정한 듀레이션을 "
    "백테스트하면 CallRank의 G3와 같은 문제가 된다. D*=2년(1년물과 3년물의 정확한 "
    "중간)은 예측 신호 없이 정의되는 고정점이라, 검증 가능한 기준선으로 쓴다. "
    "실제 City AI 모델이 갖춰지면 이 자리에 매월 조정되는 실제 D*를 넣을 수 있다."
)


def build_duration_performance_context(db: Session, as_of: date) -> dict:
    """MetroGuard 성과 페이지 컨텍스트 — D*=2년 고정 배분 백테스트.

    universe_codes/benchmark_code는 ingest_korean_equity_prices.py의
    BOND_ETF_SHORT/BOND_ETF_LONG(통안채1년/국고채3년)이다. 벤치마크는
    3년물(BOND_ETF_LONG) 100% 고정("아무 조정도 하지 않았을 때") 배분이다.

    유니버스가 이 두 자산뿐이라 벤치마크를 유니버스 밖으로 빼면 리스크패리티
    최소 자산요건(2개)을 못 채운다 — benchmark_in_universe=True로 벤치마크를
    유니버스에 남긴다. weight_fn 쪽 전략 비중(1년물·3년물 동일가중)과 벤치마크
    쪽 비중(3년물 100%)은 서로 다른 배분 규칙이며 독립적으로 계산된다.
    """
    from app.computation.backtest.engine import buy_and_hold
    from app.ingestion.jobs.ingest_korean_equity_prices import BOND_ETF_LONG, BOND_ETF_SHORT

    return build_performance_context(
        db,
        as_of,
        universe_codes=[BOND_ETF_SHORT, BOND_ETF_LONG],
        benchmark_code=BOND_ETF_LONG,
        weight_fn=buy_and_hold([0.5, 0.5]),
        strategy_label="D*=2년 고정 배분(1년물·3년물 동일가중)",
        strategy_disclosure=FIXED_ALLOCATION_DISCLOSURE_TEMPLATE.format(
            label="D*=2년(1년물·3년물 동일가중)"
        ),
        benchmark_in_universe=True,
    )


@dataclass(frozen=True)
class PriceHistory:
    """모든 자산이 공통으로 관측된 거래일만 남긴 정렬된 가격 패널."""

    codes: list[str]
    dates: list[date]
    prices: np.ndarray  # (기간 × 자산)

    @property
    def n_observations(self) -> int:
        return len(self.dates)

    def returns_panel(self) -> np.ndarray:
        """(기간-1 × 자산) 일간 수익률."""
        return np.column_stack(
            [returns_from_prices(self.prices[:, j]) for j in range(self.prices.shape[1])]
        )

    def return_dates(self) -> list[date]:
        """returns_panel의 각 행에 대응하는 날짜(수익률이 실현된 날)."""
        return self.dates[1:]


def load_price_history(db: Session, as_of: date, codes: list[str]) -> PriceHistory:
    """as_of 시점에 알 수 있었던 종가 이력을 공통 거래일로 정렬해 가져온다.

    `visible_as_of()`로 knowledge_date 필터를 걸어, 그 시점에 아직 취득하지
    않았던 값이 백테스트에 새어 들어가지 못하게 한다. 엔진의 슬라이싱 방어와
    이중으로 겹쳐, 데이터 계층과 계산 계층 양쪽에서 룩어헤드를 막는다.

    자산마다 거래일이 다를 수 있으므로(휴장일·상장일 차이) **모든 자산이 값을
    가진 날짜만** 남긴다. 결측을 직전값으로 채우면 그날 수익률이 0으로 잡혀
    변동성이 조직적으로 과소평가된다.
    """
    by_code: dict[str, dict[date, float]] = {}
    for code in codes:
        asset = db.query(DimAsset).filter_by(code=code).first()
        if asset is None:
            continue
        rows = (
            visible_as_of(db.query(FactMarketDaily), FactMarketDaily, as_of)
            .filter(
                FactMarketDaily.asset_id == asset.asset_id,
                FactMarketDaily.trade_date <= as_of,
                FactMarketDaily.adj_close.isnot(None),
            )
            .order_by(FactMarketDaily.trade_date.asc())
            .all()
        )
        if rows:
            by_code[code] = {r.trade_date: float(r.adj_close) for r in rows}

    present = [c for c in codes if c in by_code]
    if not present:
        return PriceHistory(codes=[], dates=[], prices=np.empty((0, 0)))

    common = set(by_code[present[0]])
    for code in present[1:]:
        common &= set(by_code[code])
    common_dates = sorted(common)

    prices = np.array([[by_code[c][d] for c in present] for d in common_dates], dtype=float)
    return PriceHistory(codes=present, dates=common_dates, prices=prices)


def to_monthly(dates: list[date], returns: np.ndarray) -> list[tuple[date, float]]:
    """일간 수익률 → (월말일자, 월간 복리수익률). GIPS 표의 입력이다.

    월간 수익률은 그 달 일간 수익률의 **복리**(∏(1+r)−1)다. 산술합을 쓰면
    변동이 큰 달에서 실제와 눈에 띄게 벌어진다.
    """
    out: list[tuple[date, float]] = []
    if len(dates) == 0:
        return out

    bucket: list[float] = []
    current = (dates[0].year, dates[0].month)
    last_date = dates[0]
    for d, r in zip(dates, returns):
        key = (d.year, d.month)
        if key != current:
            out.append((last_date, float(np.prod(1.0 + np.array(bucket)) - 1.0)))
            bucket = []
            current = key
        bucket.append(float(r))
        last_date = d
    out.append((last_date, float(np.prod(1.0 + np.array(bucket)) - 1.0)))
    return out


def _pct(value: float | None, digits: int = 2, signed: bool = False) -> str:
    if value is None:
        return "—"
    sign = "+" if signed else ""
    return f"{value * 100:{sign}.{digits}f}%"


def _num(value: float | None, digits: int = 2) -> str:
    if value is None:
        return "—"
    text = f"{value:.{digits}f}"
    # -0.0001은 "-0.00"으로 찍힌다. 반올림해서 0이 된 값에 음수 부호를 남기면
    # 독자가 실제로 음수인 줄 안다(베타 -0.00으로 렌더링에서 확인).
    return text.lstrip("-") if float(text) == 0 else text


def build_risk_metric_rows(
    portfolio: np.ndarray, benchmark: np.ndarray | None, risk_free_rate: float = 0.0
) -> list[list[str]]:
    """리스크·성과 지표 표. 정의되지 않는 지표는 '—'로 남긴다(0으로 채우지 않는다).

    각 행은 [지표, 포트폴리오, 벤치마크] 형식이다. 벤치마크 병기는 GIPS 요건이며,
    절대수익 단독 제시는 인정되지 않는다.
    """
    def column(r: np.ndarray) -> dict[str, str]:
        dd = max_drawdown(r)
        return {
            "연환산 수익률": _pct(annualized_return(r), signed=True),
            "연환산 변동성": _pct(annualized_volatility(r)),
            "Sharpe": _num(sharpe_ratio(r, risk_free_rate)),
            "Sortino": _num(sortino_ratio(r)),
            "Calmar": _num(calmar_ratio(r)),
            "최대낙폭": _pct(dd.max_drawdown, signed=True),
            "낙폭 회복": "미회복" if dd.recovery_index is None else f"{dd.recovery_index - dd.trough_index}거래일",
            # historical_var/conditional_var는 손실을 **양수**로 준다. signed=True를
            # 붙이면 "+3.10%"가 돼 이익처럼 읽히므로 부호 없이 손실 크기로 표기한다.
            "VaR 95% (일간 손실)": _pct(historical_var(r)),
            "CVaR 95% (일간 손실)": _pct(conditional_var(r)),
        }

    p = column(portfolio)
    b = column(benchmark) if benchmark is not None else {}

    rows = [[name, value, b.get(name, "—")] for name, value in p.items()]

    if benchmark is not None:
        rows.append(["베타", _num(beta(portfolio, benchmark)), "1.00"])
        rows.append(["추적오차", _pct(tracking_error(portfolio, benchmark)), "—"])
        rows.append(["정보비율", _num(information_ratio(portfolio, benchmark)), "—"])
    return rows


def _cumulative_curve(returns: np.ndarray) -> list[float]:
    """시작을 1.0으로 둔 누적 성장 곡선(선행 1.0 제외 — 날짜와 1:1 대응)."""
    return np.cumprod(1.0 + returns).tolist()


def _performance_charts(
    labels: list[str],
    strategy_curve: list[float],
    benchmark_curve: list[float],
    benchmark_code: str,
    roll_labels: list[str],
    roll_sharpe: rolling.RollingSeries,
    roll_corr: rolling.RollingSeries,
    roll_vol: rolling.RollingSeries,
    strategy_label: str = "리스크패리티 중립 배분",
) -> dict:
    """차트 3종을 data URI로 만든다.

    롤링 Sharpe와 상관계수는 둘 다 무차원이라 한 축에 얹을 수 있지만, 변동성은
    비율(0.15)이라 같은 축에 그리면 선이 바닥에 붙는다 — 별도 차트로 뺀다.
    """
    # 컴퓨테이션 계층이 렌더링 계층을 임포트하는 것을 모듈 로드 시점으로 끌어올리지
    # 않는다(WeasyPrint/GTK 미설치 환경에서 임포트만으로 죽는 것을 피하는 기존 패턴).
    from app.rendering.chart_service import line_chart

    charts = {
        "performance_curve_chart_uri": line_chart(
            labels,
            {strategy_label: strategy_curve, benchmark_code: benchmark_curve},
        )
    }

    if not roll_sharpe.is_empty:
        charts["rolling_ratio_chart_uri"] = line_chart(
            roll_labels,
            {
                f"12M 롤링 Sharpe": roll_sharpe.to_plot_values(),
                f"12M 롤링 {benchmark_code} 상관계수": roll_corr.to_plot_values(),
            },
        )
        charts["rolling_volatility_chart_uri"] = line_chart(
            roll_labels,
            {"12M 롤링 연환산 변동성(%)": [v * 100 for v in roll_vol.to_plot_values()]},
        )
    else:
        charts["rolling_ratio_chart_uri"] = None
        charts["rolling_volatility_chart_uri"] = None

    return charts


def _pending(reason: str, observations: int) -> dict:
    from app.computation.performance_disclosure import build_performance_pending_context

    context = build_performance_pending_context()
    context.update({
        "performance_available": False,
        "performance_data_status": (
            f"{reason} (확보 {observations}거래일 / 최소 {MIN_BACKTEST_OBSERVATIONS}거래일 필요)"
        ),
    })
    return context


def build_performance_context(
    db: Session,
    as_of: date,
    universe_codes: list[str],
    benchmark_code: str,
    *,
    cost_model: CostModel = DEFAULT_COST_MODEL,
    constraints: ConstraintSet = DEFAULT_CONSTRAINTS,
    risk_free_rate: float = 0.0,
    weight_fn: WeightFn | None = None,
    weight_fn_min_observations: int = MIN_COVARIANCE_OBSERVATIONS,
    strategy_label: str = "리스크패리티 중립 배분",
    strategy_disclosure: str = NEUTRAL_STRATEGY_DISCLOSURE,
    benchmark_in_universe: bool = False,
) -> dict:
    """성과·리스크 페이지 컨텍스트를 만든다.

    이력이 부족하면 숫자를 만들어내지 않고 `performance_disclosure`의 보류
    컨텍스트를 그대로 돌려준다 — 페이지는 "왜 비어 있는지"를 싣는다.

    weight_fn: 생략하면 CallRank 기본값(공분산 기반 리스크패리티)을 쓴다.
        MetroGuard처럼 리스크패리티가 아니라 고정 배분(buy_and_hold)이 맞는
        전략은 이 파라미터로 주입한다 — 로직은 동일하고 "무엇을 배분 규칙으로
        쓸지"만 다르다.

    benchmark_in_universe: CallRank처럼 벤치마크(SPY)가 유니버스 밖 별도
        자산이면 False(기본값) — 유니버스에서 벤치마크를 제외한다. MetroGuard처럼
        유니버스가 자산 2개뿐이고 그중 하나(3년물)를 벤치마크로도 써야 최소
        자산요건(2개)을 채울 수 있는 경우 True로 둔다 — 이때 벤치마크는
        유니버스에서 제외되지 않고 100% buy-and-hold 비교선으로만 별도 산출된다.
    """
    codes_to_load = universe_codes if benchmark_in_universe else universe_codes + [benchmark_code]
    history = load_price_history(db, as_of, codes_to_load)

    if benchmark_code not in history.codes:
        return _pending(f"벤치마크({benchmark_code}) 가격 이력 없음", history.n_observations)

    universe = list(history.codes) if benchmark_in_universe else [
        c for c in history.codes if c != benchmark_code
    ]
    if len(universe) < 2:
        return _pending(f"유니버스 자산 {len(universe)}개 — 2개 이상 필요", history.n_observations)
    if history.n_observations < MIN_BACKTEST_OBSERVATIONS:
        return _pending("공통 거래일 부족", history.n_observations)

    panel = history.returns_panel()
    dates = history.return_dates()
    bench_col = history.codes.index(benchmark_code)
    universe_cols = [history.codes.index(c) for c in universe]

    strategy_panel = panel[:, universe_cols]
    benchmark_returns = panel[:, bench_col]

    # 월말 리밸런싱. 엔진 규약상 인덱스 t의 리밸런싱은 panel[:t]만 보므로,
    # 월말 인덱스를 넘겨도 그날 수익률을 미리 쓰는 일은 생기지 않는다.
    rebalance = periodic_rebalance_indices(dates, "M")

    # 기본 상한 25%는 11개 섹터를 가정한 값이라 유니버스가 작으면 실현 불가능하다.
    # 완화 여부를 받아 가정 문구에 명시한다(조용히 바꾸지 않는다).
    effective, relaxed = relax_cap_to_feasible(constraints, len(universe))

    resolved_weight_fn = weight_fn or from_covariance(
        lambda h: risk_parity(np.cov(h, rowvar=False, ddof=1)),
        min_observations=weight_fn_min_observations,
    )

    result = run_backtest(
        dates,
        strategy_panel,
        weight_fn=resolved_weight_fn,
        rebalance_indices=rebalance,
        cost_model=cost_model,
        constraints=effective,
    )

    if not result.rebalance_indices:
        return _pending("공분산 추정에 필요한 이력 부족 — 리밸런싱이 한 번도 집행되지 않음",
                        history.n_observations)

    # 첫 리밸런싱 전 구간은 동일가중 기본값이 만든 수익률이라 전략 성과가 아니다.
    # 그 구간을 잘라내고 평가한다 — 자르지 않으면 전략과 무관한 기간이 성과에 섞인다.
    start = result.rebalance_indices[0]
    strategy_returns = result.returns[start:]
    bench_slice = benchmark_returns[start:]
    eval_dates = dates[start:]

    monthly_p = to_monthly(eval_dates, strategy_returns)
    monthly_b = to_monthly(eval_dates, bench_slice)
    gips_rows = build_gips_table(monthly_p, monthly_b)

    roll_sharpe = rolling.rolling_sharpe(strategy_returns, ROLLING_WINDOW, risk_free_rate)
    roll_vol = rolling.rolling_volatility(strategy_returns, ROLLING_WINDOW)
    roll_corr = rolling.rolling_correlation(strategy_returns, bench_slice, ROLLING_WINDOW)

    # 평가 구간 시작을 1.0으로 두고 전략·벤치마크를 같은 출발선에서 비교한다.
    strategy_curve = _cumulative_curve(strategy_returns)
    benchmark_curve = _cumulative_curve(bench_slice)
    curve_labels = [d.strftime("%y-%m") for d in eval_dates]
    roll_labels = roll_sharpe.labels(eval_dates)

    return {
        "performance_available": True,
        "performance_hypothetical_disclosure": HYPOTHETICAL_DISCLOSURE,
        "performance_neutral_disclosure": strategy_disclosure,
        "performance_strategy_label": strategy_label,
        "performance_period": (
            f"{eval_dates[0].isoformat()} ~ {eval_dates[-1].isoformat()} "
            f"({len(eval_dates)}거래일, 자산 {len(universe)}종)"
        ),
        "performance_assumptions": (
            f"월말 리밸런싱 {len(result.rebalance_indices)}회 · "
            f"종목 상한 {effective.max_weight * 100:.1f}%"
            + (
                (
                    f" (설정값 {constraints.max_weight * 100:.0f}%는 {len(universe)}개 자산에 "
                    f"적용 불가라 1/n으로 완화 — 이 경우 모든 비중이 1/n으로 강제되어 "
                    f"리스크패리티 결과가 반영되지 않는다)"
                    if weight_fn is None
                    # weight_fn을 명시 주입한 경우(예: MetroGuard의 고정 배분)는
                    # 리스크패리티가 아니므로 위 문구가 오도한다 — 완화 사실만
                    # 중립적으로 밝힌다("리스크패리티 결과"라는 표현을 쓰지 않는다).
                    else f" (설정값 {constraints.max_weight * 100:.0f}%는 {len(universe)}개 자산에 "
                    f"적용 불가라 1/n으로 완화 — 지정된 배분 규칙이 이 상한을 넘으면 "
                    f"완화된 상한선까지 잘려 표시된다)"
                )
                if relaxed else ""
            )
            + f" · {cost_model.describe()} · "
            f"누적 거래비용 {result.total_cost * 1e4:.0f}bp · "
            f"누적 회전율 {result.total_turnover * 100:.0f}%"
        ),
        "performance_benchmark": benchmark_code,
        "risk_metric_rows": build_risk_metric_rows(
            strategy_returns, bench_slice, risk_free_rate
        ),
        "gips_rows": format_gips_table_rows(gips_rows),
        "gips_meets_minimum": meets_gips_minimum_history(gips_rows),
        "gips_minimum_note": (
            "GIPS 최소 이력 5년(완전연도 기준)을 충족한다."
            if meets_gips_minimum_history(gips_rows)
            else f"완전연도 {sum(1 for r in gips_rows if not r.is_partial_year)}개 — "
                 "GIPS 최소 이력 5년에 미달한다. 부분연도는 * 로 표시했고 연환산하지 않았다."
        ),
        "rolling_labels": roll_labels,
        "rolling_sharpe": roll_sharpe.to_plot_values(),
        "rolling_volatility": roll_vol.to_plot_values(),
        "rolling_correlation": roll_corr.to_plot_values(),
        "rolling_summaries": [
            rolling.summarize(roll_sharpe, "Sharpe", ROLLING_WINDOW_LABEL).describe(),
            rolling.summarize(roll_vol, "변동성", ROLLING_WINDOW_LABEL).describe("{:.1%}"),
            rolling.summarize(
                roll_corr, f"{benchmark_code} 상관계수", ROLLING_WINDOW_LABEL
            ).describe(),
        ],
        "equity_curve": strategy_curve,
        "benchmark_curve": benchmark_curve,
        "equity_curve_labels": curve_labels,
        "performance_data_status": (
            f"공통 거래일 {history.n_observations}일 · 평가 구간 {len(eval_dates)}일"
        ),
        **_performance_charts(
            curve_labels, strategy_curve, benchmark_curve, benchmark_code,
            roll_labels, roll_sharpe, roll_corr, roll_vol,
            strategy_label=strategy_label,
        ),
    }
