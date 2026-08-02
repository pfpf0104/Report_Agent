"""3개 리포트(CallRank·MetroGuard·밸류에이션)를 가로지르는 크로스에셋 상관 행렬.

MASTER_PLAN Phase 3-2. 지금까지 세 리포트는 서로 대화하지 않았다(G9) — 각자
자기 유니버스만 백테스트·평가했다. 이 모듈은 셋의 대표 자산을 한 상관행렬에
놓아, "미국 섹터가 흔들릴 때 한국 채권·주식은 같이 흔들리는가"를 실측으로
보여준다.

레짐 분류(성장×인플레 4분면, Phase 3-1)는 아직 하지 않는다 — 그건 GDP·CPI
같은 거시지표가 필요한데 지금 DB에는 없다. 여기서 계산하는 건 그보다 훨씬
약한 주장이다: "지난 N년간 이 자산들의 일간 수익률이 실제로 얼마나 같이
움직였는가"라는 검증 가능한 사실뿐이다.

## 대표 자산 선정 기준

전체 유니버스(미국 11개 섹터+SPY, 한국 채권 2종, 한국 주식 2종)를 전부 넣으면
16×16 행렬이 되어 리포트 페이지 하나에 읽을 수 있는 크기가 아니다. 각 리포트의
벤치마크/대표 자산 하나씩만 뽑는다 — 이건 그 리포트의 성과 페이지가 이미 쓰고
있는 벤치마크와 같은 자산이라, "이 리포트가 이미 성과를 보여준 그 벤치마크가
다른 리포트의 벤치마크와 어떻게 움직이는가"로 읽을 수 있다.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import numpy as np
from sqlalchemy.orm import Session

from app.computation.risk.report_context import MIN_BACKTEST_OBSERVATIONS, load_price_history

def _default_representative_assets() -> dict[str, str]:
    """리포트별 대표 자산 하나씩 — 각 리포트 성과 페이지의 벤치마크와 동일하다.

    문자열을 여기 다시 하드코딩하지 않고 각 리포트 모듈의 실제 상수를
    그대로 참조한다 — 그 리포트의 벤치마크가 바뀌면(예: MetroGuard가 다른
    만기의 채권으로 벤치마크를 바꾸면) 이 페이지도 자동으로 따라간다. 세
    모듈(ridge_sector_rank/ingest_korean_equity_prices/residual_income_model)이
    전부 cross_asset을 import하므로, 반대 방향 import는 함수 내부에서 지연
    수행해 순환 참조를 피한다.
    """
    from app.computation.quant.ridge_sector_rank import PERFORMANCE_BENCHMARK
    from app.computation.valuation.residual_income_model import STOCK_CODE
    from app.ingestion.jobs.ingest_korean_equity_prices import BOND_ETF_LONG

    return {
        PERFORMANCE_BENCHMARK: "CallRank(미국 섹터 벤치마크)",
        BOND_ETF_LONG: "MetroGuard(국고채3년)",
        STOCK_CODE["삼성전자"]: "밸류에이션(삼성전자)",
    }

# 최소 자산 2개는 있어야 상관을 정의할 수 있다.
MIN_CROSS_ASSET_ASSETS = 2


@dataclass(frozen=True)
class CrossAssetContext:
    available: bool
    codes: list[str]
    labels: list[str]
    correlation: list[list[float]]  # codes와 같은 순서의 정방행렬
    n_observations: int
    period: str
    data_status: str


def _pending(reason: str, n_observations: int = 0) -> CrossAssetContext:
    return CrossAssetContext(
        available=False, codes=[], labels=[], correlation=[], n_observations=n_observations,
        period="", data_status=reason,
    )


def build_cross_asset_correlation(
    db: Session, as_of: date, asset_codes: dict[str, str] | None = None
) -> CrossAssetContext:
    """asset_codes(코드→표시 라벨)의 일간 수익률 상관행렬을 실측으로 계산한다.

    생략하면 _default_representative_assets()(3개 리포트 대표 자산)를 쓴다.
    이력이 부족하면(자산 2개 미만 확보, 최소 관측치 미달) 숫자를 만들어내지
    않고 보류 컨텍스트를 반환한다 — 성과 페이지와 동일한 원칙.
    """
    universe = asset_codes or _default_representative_assets()
    codes = list(universe.keys())
    history = load_price_history(db, as_of, codes)

    if len(history.codes) < MIN_CROSS_ASSET_ASSETS:
        return _pending(
            f"자산 {len(history.codes)}개 확보 — {MIN_CROSS_ASSET_ASSETS}개 이상 필요",
            history.n_observations,
        )
    if history.n_observations < MIN_BACKTEST_OBSERVATIONS:
        return _pending(
            f"공통 거래일 {history.n_observations}일 — 최소 {MIN_BACKTEST_OBSERVATIONS}일 필요",
            history.n_observations,
        )

    panel = history.returns_panel()
    # np.corrcoef는 행을 변수로 보므로 (자산 × 기간) 전치가 필요하다.
    corr = np.corrcoef(panel, rowvar=False)

    dates = history.return_dates()
    return CrossAssetContext(
        available=True,
        codes=history.codes,
        labels=[universe[c] for c in history.codes],
        correlation=corr.tolist(),
        n_observations=history.n_observations,
        period=f"{dates[0].isoformat()} ~ {dates[-1].isoformat()} ({len(dates)}거래일)",
        data_status=f"공통 거래일 {history.n_observations}일 기준 실측 상관계수",
    )


def build_cross_asset_report_context(
    db: Session, as_of: date, asset_codes: dict[str, str] | None = None
) -> dict:
    """report_context 계열과 동일한 dict-반환 컨벤션 — Jinja 템플릿에 그대로 풀어 쓴다.

    asset_codes는 테스트에서 대표 자산을 격리된 코드로 바꿔치기할 때 쓴다
    (운영 DB에 이미 SPY/114260/005930 데이터가 있어 기본값으로는 보류 경로를
    재현할 수 없다).
    """
    ctx = build_cross_asset_correlation(db, as_of, asset_codes)
    if not ctx.available:
        return {
            "cross_asset_available": False,
            "cross_asset_data_status": ctx.data_status,
        }

    from app.rendering.chart_service import correlation_heatmap

    rows = []
    for i, row_label in enumerate(ctx.labels):
        rows.append(
            [row_label] + [f"{ctx.correlation[i][j]:+.2f}" for j in range(len(ctx.labels))]
        )

    return {
        "cross_asset_available": True,
        "cross_asset_period": ctx.period,
        "cross_asset_data_status": ctx.data_status,
        "cross_asset_labels": ctx.labels,
        "cross_asset_table_rows": rows,
        "cross_asset_heatmap_chart_uri": correlation_heatmap(ctx.labels, ctx.correlation),
        "cross_asset_disclosure": (
            f"이 페이지는 3개 리포트 각각의 대표 자산 {len(ctx.labels)}종만 다룬다 "
            f"(비대각 상관계수 {len(ctx.labels) * (len(ctx.labels) - 1) // 2}개) — "
            "전체 자산군을 아우르는 크로스에셋 분석이 아니라 세 리포트를 잇는 "
            "최소 연결점이다. 위 상관계수는 지정된 기간의 실제 일간 수익률로 "
            "계산한 통계값이며, 레짐(성장·인플레이션 국면) 분류나 인과관계를 "
            "나타내지 않는다. 상관은 시간에 따라 변하므로 특정 기간의 관측값을 "
            "미래에 그대로 적용할 수 없다."
        ),
    }
