"""랭킹 → 비중 페이지 컨텍스트 테스트.

두 경로를 모두 확인한다:
  - 이력 부족 → 합성 공분산을 만들지 않고 대기 상태
  - 이력 충분 → 실제 공분산으로 비중 산출

두 번째가 특히 중요하다. DB가 비어 있는 현재 상태만 확인하면 "우아하게 실패하는
코드"만 검증한 셈이고, Phase 0 백필 이후에야 계산 경로가 처음 돌아가게 된다.
"""
from datetime import date, timedelta

import numpy as np
import pytest

from app.computation.portfolio.report_context import MIN_OBSERVATIONS, build_portfolio_context
from app.db.base import SessionLocal
from app.db.models.dim_asset import AssetType, DimAsset
from app.db.models.fact_market_daily import FactMarketDaily

ETF_BY_SECTOR = {"Energy": "TESTXLE", "Tech": "TESTXLK", "Financials": "TESTXLF"}
CODES = list(ETF_BY_SECTOR.values())
RANKING = [
    {"sector": "Energy", "score": 1.0},
    {"sector": "Tech", "score": 0.6},
    {"sector": "Financials", "score": 0.2},
]


@pytest.fixture()
def db():
    session = SessionLocal()
    yield session
    session.query(FactMarketDaily).filter(FactMarketDaily.asset_id.in_(
        session.query(DimAsset.asset_id).filter(DimAsset.code.in_(CODES))
    )).delete(synchronize_session=False)
    session.query(DimAsset).filter(DimAsset.code.in_(CODES)).delete(synchronize_session=False)
    session.commit()
    session.close()


def _seed_history(db, code: str, n_days: int, daily_vol: float, seed: int) -> None:
    """결정적 시드로 가격 이력을 만든다.

    합성이지만 리포트에 실리는 숫자가 아니라 **테스트 픽스처**다 — 공분산 계산
    경로가 실제로 도는지 확인하는 용도이며, 이 값이 사용자에게 보이지는 않는다.
    """
    asset = DimAsset(asset_type=AssetType.ETF.value, code=code, name_kr=code, currency="USD")
    db.add(asset)
    db.commit()
    db.refresh(asset)

    rng = np.random.default_rng(seed)
    price = 100.0
    start = date(2026, 7, 30) - timedelta(days=n_days * 2)
    added = 0
    day = start
    while added < n_days:
        if day.weekday() < 5:
            price *= 1 + rng.normal(0.0003, daily_vol)
            db.add(FactMarketDaily(
                asset_id=asset.asset_id, trade_date=day, knowledge_date=day,
                close=price, adj_close=price, source="test",
            ))
            added += 1
        day += timedelta(days=1)
    db.commit()


def test_pending_when_no_price_history(db):
    ctx = build_portfolio_context(db, date(2026, 7, 30), RANKING, ETF_BY_SECTOR)
    assert ctx["portfolio_available"] is False
    assert "portfolio_pending_title" in ctx
    assert "0개" in ctx["portfolio_data_status"]


def test_pending_when_history_is_too_short(db):
    """최소 요건 미달이면 비중을 만들지 않는다 — 짧은 이력의 공분산은 불안정하다."""
    for i, code in enumerate(CODES):
        _seed_history(db, code, n_days=50, daily_vol=0.01, seed=i)
    ctx = build_portfolio_context(db, date(2026, 7, 30), RANKING, ETF_BY_SECTOR)
    assert ctx["portfolio_available"] is False


def test_produces_weights_when_history_is_sufficient(db):
    """실제 계산 경로 — Phase 0 백필 이후 처음 돌아갈 경로다."""
    for i, code in enumerate(CODES):
        _seed_history(db, code, n_days=MIN_OBSERVATIONS + 10, daily_vol=0.01, seed=i)

    ctx = build_portfolio_context(db, date(2026, 7, 30), RANKING, ETF_BY_SECTOR)

    assert ctx["portfolio_available"] is True
    assert len(ctx["portfolio_rows"]) == 3
    assert "portfolio_assumptions" in ctx
    assert "portfolio_cost_note" in ctx

    # 최종 비중 합이 100%여야 한다(표시 문자열에서 파싱)
    finals = [float(row[4].rstrip("%")) for row in ctx["portfolio_rows"]]
    assert sum(finals) == pytest.approx(100.0, abs=0.2)

    # 위험 기여 합도 100%
    risks = [float(row[5].rstrip("%")) for row in ctx["portfolio_rows"]]
    assert sum(risks) == pytest.approx(100.0, abs=0.2)


def test_weights_respect_the_configured_cap(db):
    from app.computation.portfolio.constraints import ConstraintSet

    for i, code in enumerate(CODES):
        _seed_history(db, code, n_days=MIN_OBSERVATIONS + 10, daily_vol=0.01, seed=i)

    ctx = build_portfolio_context(
        db, date(2026, 7, 30), RANKING, ETF_BY_SECTOR,
        constraints=ConstraintSet(max_weight=0.40),
    )
    finals = [float(row[4].rstrip("%")) for row in ctx["portfolio_rows"]]
    assert max(finals) <= 40.0 + 0.2, f"상한 위반: {finals}"


def test_higher_ranked_sector_gets_more_weight_than_neutral(db):
    """신호가 실제로 반영되는지 — 1위 섹터의 최종 비중이 중립 비중보다 커야 한다.
    변동성을 동일하게 맞춰 중립 비중이 균등해지도록 한 뒤 확인한다."""
    for i, code in enumerate(CODES):
        _seed_history(db, code, n_days=MIN_OBSERVATIONS + 10, daily_vol=0.01, seed=i)

    from app.computation.portfolio.constraints import ConstraintSet

    ctx = build_portfolio_context(
        db, date(2026, 7, 30), RANKING, ETF_BY_SECTOR,
        constraints=ConstraintSet(max_weight=0.90), tilt_strength=0.5,
    )
    top = ctx["portfolio_rows"][0]
    neutral, final = float(top[3].rstrip("%")), float(top[4].rstrip("%"))
    assert final > neutral, f"1위 섹터 비중이 중립보다 커야 한다: {neutral} → {final}"


def test_zero_tilt_keeps_neutral_weights(db):
    """기울기 0이면 신호가 비중을 움직이지 않아야 한다.

    상한이 걸리지 않는 값(90%)을 쓴다 — 기본 상한은 3개 자산에서 1/n으로 완화돼
    비중을 전부 1/n으로 강제하므로, 그 상태로는 기울기 효과를 분리할 수 없다."""
    from app.computation.portfolio.constraints import ConstraintSet

    for i, code in enumerate(CODES):
        _seed_history(db, code, n_days=MIN_OBSERVATIONS + 10, daily_vol=0.01, seed=i)

    ctx = build_portfolio_context(
        db, date(2026, 7, 30), RANKING, ETF_BY_SECTOR,
        constraints=ConstraintSet(max_weight=0.90), tilt_strength=0.0,
    )
    for row in ctx["portfolio_rows"]:
        assert float(row[3].rstrip("%")) == pytest.approx(float(row[4].rstrip("%")), abs=0.15)


def test_cap_below_one_over_n_is_relaxed_and_disclosed(db):
    """백필 중 정상적으로 발생하는 상황: 기본 상한 25%는 자산이 3개뿐이면 불가능하다.
    예외로 리포트를 깨뜨리는 대신 1/n으로 완화하되, 그 사실과 부작용(동일가중 강제)을
    가정 문구에 명시해야 한다."""
    for i, code in enumerate(CODES):
        _seed_history(db, code, n_days=MIN_OBSERVATIONS + 10, daily_vol=0.01, seed=i)

    ctx = build_portfolio_context(db, date(2026, 7, 30), RANKING, ETF_BY_SECTOR)

    assert ctx["portfolio_available"] is True, "예외로 죽지 않아야 한다"
    assert "완화" in ctx["portfolio_assumptions"]
    assert "리스크패리티 결과가 반영되지 않는다" in ctx["portfolio_assumptions"]

    finals = [float(row[4].rstrip("%")) for row in ctx["portfolio_rows"]]
    assert finals == pytest.approx([100 / 3] * 3, abs=0.1), "1/n 강제 결과"


def test_respects_point_in_time_visibility(db):
    """as_of 이후에 알게 된 가격은 공분산 추정에 들어가면 안 된다."""
    for i, code in enumerate(CODES):
        _seed_history(db, code, n_days=MIN_OBSERVATIONS + 10, daily_vol=0.01, seed=i)

    # 모든 행의 knowledge_date를 미래로 밀면 아무것도 보이지 않아야 한다
    db.query(FactMarketDaily).filter(FactMarketDaily.asset_id.in_(
        db.query(DimAsset.asset_id).filter(DimAsset.code.in_(CODES))
    )).update({"knowledge_date": date(2027, 1, 1)}, synchronize_session=False)
    db.commit()

    ctx = build_portfolio_context(db, date(2026, 7, 30), RANKING, ETF_BY_SECTOR)
    assert ctx["portfolio_available"] is False


def test_empty_ranking_returns_pending(db):
    ctx = build_portfolio_context(db, date(2026, 7, 30), [], ETF_BY_SECTOR)
    assert ctx["portfolio_available"] is False
