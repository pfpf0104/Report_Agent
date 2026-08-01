"""FMP에서 ETF/주식 시세를 가져와 fact_market_daily에 적재하는 배치.

TODO: SYMBOLS를 하드코딩 대신 dim_asset(asset_type='ETF' 등)에서 동적으로
조회하도록 바꿔야 한다. 지금은 CallRank가 실제로 쓰는 두 개(XLE, SPY)만 다룬다.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import httpx
from sqlalchemy.orm import Session

from app.db.base import SessionLocal
from app.db.models.dim_asset import AssetType, DimAsset
from app.db.models.fact_market_daily import FactMarketDaily
from app.ingestion.connectors.fmp_client import fetch_quote
from app.ingestion.run_tracker import track_ingestion_run

SYMBOLS = ["XLE", "SPY"]


async def _fetch_all_quotes(symbols: list[str]) -> dict[str, dict]:
    async with httpx.AsyncClient() as client:
        return {symbol: await fetch_quote(client, symbol) for symbol in symbols}


def _get_or_create_asset(db: Session, symbol: str) -> DimAsset:
    asset = db.query(DimAsset).filter_by(code=symbol).first()
    if asset is None:
        asset = DimAsset(asset_type=AssetType.ETF.value, code=symbol, name_kr=symbol, currency="USD")
        db.add(asset)
        db.commit()
        db.refresh(asset)
    return asset


def _upsert_quote(db: Session, asset: DimAsset, trade_date, quote: dict) -> None:
    row = db.query(FactMarketDaily).filter_by(asset_id=asset.asset_id, trade_date=trade_date).first()
    if row is None:
        # knowledge_date = 인제스천 시점. 이 job은 당일 시세를 당일 조회하므로
        # trade_date와 같지만, 과거분 백필 시에는 달라진다(그때 알 수 없던 값이
        # 아니라 지금 알게 된 값이라는 사실을 정확히 기록한다).
        row = FactMarketDaily(asset_id=asset.asset_id, trade_date=trade_date, knowledge_date=trade_date)
        db.add(row)
    row.open = quote.get("open")
    row.high = quote.get("dayHigh")
    row.low = quote.get("dayLow")
    row.close = quote.get("price")
    row.adj_close = quote.get("price")
    row.volume = quote.get("volume")
    row.source = "fmp"


def run() -> None:
    db = SessionLocal()
    try:
        with track_ingestion_run(db, "fmp_equity_prices") as ingestion:
            quotes = asyncio.run(_fetch_all_quotes(SYMBOLS))
            trade_date = datetime.now(timezone.utc).date()

            for symbol, quote in quotes.items():
                asset = _get_or_create_asset(db, symbol)
                _upsert_quote(db, asset, trade_date, quote)

            db.commit()
            ingestion.raw_archive_path = "data/raw_archive/fmp"
    finally:
        db.close()
