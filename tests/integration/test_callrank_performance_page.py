"""CallRank 성과 페이지가 컨텍스트와 실제로 맞물리는지 — 템플릿까지 렌더링해 본다.

컨텍스트 단위 테스트는 딕셔너리 키만 본다. 템플릿이 다른 이름을 참조하고 있으면
Jinja는 조용히 빈 문자열을 내보내고, 리포트에는 빈칸이 실린 채 배포된다.
그래서 여기서는 HTML을 실제로 렌더링해 숫자가 지면에 나타나는지 확인한다.
(PDF까지 가지 않는 이유: 텍스트가 페이지에 들어갔는지는 HTML 단계에서 확인
가능하고, WeasyPrint는 느린 데다 GTK 네이티브 의존성이 있다.)
"""
from datetime import date, timedelta

import numpy as np
import pytest

from app.computation.quant.ridge_sector_rank import (
    PERFORMANCE_BENCHMARK,
    build_callrank_context,
)
from app.computation.quant.sector_embeddings import SECTOR_ETF_BY_NAME
from app.computation.risk.report_context import MIN_BACKTEST_OBSERVATIONS
from app.db.base import SessionLocal
from app.db.models.dim_asset import AssetType, DimAsset
from app.db.models.fact_market_daily import FactMarketDaily
from app.rendering.pdf_service import render_html

CODES = list(SECTOR_ETF_BY_NAME.values()) + [PERFORMANCE_BENCHMARK]
AS_OF = date(2026, 8, 1)


def _cleanup(session):
    ids = session.query(DimAsset.asset_id).filter(DimAsset.code.in_(CODES))
    session.query(FactMarketDaily).filter(FactMarketDaily.asset_id.in_(ids)).delete(
        synchronize_session=False
    )
    session.query(DimAsset).filter(DimAsset.code.in_(CODES)).delete(synchronize_session=False)
    session.commit()


@pytest.fixture()
def db():
    session = SessionLocal()
    _cleanup(session)
    yield session
    _cleanup(session)
    session.close()


def _seed_universe(session, n_days: int) -> None:
    """as_of에서 거꾸로 n_days 영업일치 가격을 심는다."""
    dates: list[date] = []
    d = AS_OF
    while len(dates) < n_days:
        if d.weekday() < 5:
            dates.append(d)
        d -= timedelta(days=1)
    dates.reverse()

    for i, code in enumerate(CODES):
        rng = np.random.default_rng(900 + i)
        returns = rng.normal(0.0003, 0.008 + 0.001 * i, size=n_days - 1)
        prices = 100.0 * np.concatenate([[1.0], np.cumprod(1.0 + returns)])

        asset = DimAsset(asset_type=AssetType.ETF.value, code=code, name_kr=code, currency="USD")
        session.add(asset)
        session.commit()
        session.refresh(asset)
        session.bulk_save_objects([
            FactMarketDaily(
                asset_id=asset.asset_id, trade_date=dt, knowledge_date=dt,
                close=float(p), adj_close=float(p), source="test",
            )
            for dt, p in zip(dates, prices)
        ])
    session.commit()


def test_pending_page_renders_when_there_is_no_price_history(db):
    context = build_callrank_context(db, AS_OF)
    html = render_html("callrank/report.html", context)

    assert context["performance_available"] is False
    assert "성과 보고 준비 상태" in html
    assert context["performance_data_status"] in html
    # 보류 상태에서 성과 표가 새어 나오면 안 된다
    assert "GIPS COMPOSITE" not in html


def test_performance_pages_render_with_real_numbers(db):
    _seed_universe(db, MIN_BACKTEST_OBSERVATIONS + 400)
    context = build_callrank_context(db, AS_OF)
    html = render_html("callrank/report.html", context)

    assert context["performance_available"] is True
    assert "위험 균등 배분 백테스트" in html
    assert "GIPS COMPOSITE" in html

    # 지표 표의 값이 실제로 지면에 들어갔는가(템플릿이 다른 키를 봤다면 빠진다)
    metrics = {row[0]: row[1] for row in context["risk_metric_rows"]}
    assert metrics["Sharpe"] in html
    assert metrics["최대낙폭"] in html
    assert context["performance_assumptions"] in html

    # 차트는 base64 data URI로 인라인된다
    assert "data:image/png;base64," in html


def test_hypothetical_disclosure_appears_on_the_same_page_as_the_numbers(db):
    """숫자만 있고 공시가 빠진 페이지가 배포되면 안 된다."""
    _seed_universe(db, MIN_BACKTEST_OBSERVATIONS + 400)
    html = render_html("callrank/report.html", build_callrank_context(db, AS_OF))

    assert "가설적 백테스트 — 실현 성과가 아니다" in html
    assert "CallRank 섹터 점수로 비중을 기울인 결과가 아니다" in html


def test_removed_hardcoded_backtest_figure_stays_removed(db):
    """G2 잔재 회귀 — 근거 없는 '39.6%' 같은 수치가 푸터에 되살아나지 않는지."""
    html = render_html("callrank/report.html", build_callrank_context(db, AS_OF))
    assert "39.6%" not in html
    assert "42.1%" not in html
