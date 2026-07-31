"""CallRank 리포트 context 빌더.

정식 PCA·Ridge 섹터 랭킹 모델은 별도로 구현 예정이며, 여기서는 dim_asset/
fact_market_daily에서 실제로 조회한 MTD(month-to-date) 수익률을 바탕으로
리포트 첫 페이지에 필요한 메트릭 카드를 구성한다.
"""
from __future__ import annotations

from datetime import date

from sqlalchemy.orm import Session

from app.db.models.dim_asset import DimAsset
from app.db.models.fact_market_daily import FactMarketDaily


def _tone(value: float | None) -> str | None:
    if value is None:
        return None
    if value > 0:
        return "up"
    if value < 0:
        return "down"
    return None


def _mtd_return(db: Session, asset_id: int, as_of: date) -> float | None:
    month_start = as_of.replace(day=1)

    start_row = (
        db.query(FactMarketDaily)
        .filter(FactMarketDaily.asset_id == asset_id, FactMarketDaily.trade_date < month_start)
        .order_by(FactMarketDaily.trade_date.desc())
        .first()
    )
    end_row = (
        db.query(FactMarketDaily)
        .filter(FactMarketDaily.asset_id == asset_id, FactMarketDaily.trade_date <= as_of)
        .order_by(FactMarketDaily.trade_date.desc())
        .first()
    )
    if not start_row or not end_row or not start_row.adj_close:
        return None
    return float(end_row.adj_close / start_row.adj_close - 1) * 100


def build_callrank_context(db: Session, as_of: date, leading_asset_code: str = "XLE") -> dict:
    asset = db.query(DimAsset).filter_by(code=leading_asset_code).first()

    mtd_return = _mtd_return(db, asset.asset_id, as_of) if asset else None
    asset_label = asset.name_kr if asset else leading_asset_code

    cards = [
        {
            "label": f"{as_of.month}월 MTD {asset_label}",
            "value": f"{mtd_return:+.2f}%" if mtd_return is not None else "데이터 없음",
            "caption": f"{leading_asset_code} 보유 경로 · fact_market_daily 기준",
            "tone": _tone(mtd_return),
        },
    ]

    return {
        "as_of": as_of.isoformat(),
        "generated_at": as_of.isoformat(),
        "cards": cards,
    }
