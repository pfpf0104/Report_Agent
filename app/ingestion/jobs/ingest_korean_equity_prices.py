"""KIS에서 국내 주식(삼성전자·SK하이닉스) 현재가를 가져와 fact_market_daily에 적재한다.

밸류에이션 리포트(residual_income_model.py)의 CURRENT_PRICE 하드코딩을
대체할 실데이터 소스다 — 다만 이 배치만으로는 자동 대체되지 않고,
CURRENT_PRICE를 이 테이블 조회로 바꾸는 건 별도 작업이다.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import httpx
from sqlalchemy.orm import Session

from app.db.base import SessionLocal
from app.db.models.dim_asset import AssetType, DimAsset
from app.db.models.fact_market_daily import FactMarketDaily
from app.ingestion.connectors.kis_client import fetch_stock_price
from app.ingestion.run_tracker import track_ingestion_run

# code: (종목코드, 한글명)
SYMBOLS = {
    "005930": "삼성전자",
    "000660": "SK하이닉스",
}


async def _fetch_all_prices(stock_codes: list[str]) -> dict[str, dict]:
    async with httpx.AsyncClient() as client:
        return {code: await fetch_stock_price(client, code) for code in stock_codes}


def _get_or_create_asset(db: Session, code: str, name_kr: str) -> DimAsset:
    asset = db.query(DimAsset).filter_by(code=code).first()
    if asset is None:
        asset = DimAsset(asset_type=AssetType.EQUITY.value, code=code, name_kr=name_kr, currency="KRW")
        db.add(asset)
        db.commit()
        db.refresh(asset)
    return asset


def _upsert_price(db: Session, asset: DimAsset, trade_date, output: dict) -> None:
    row = db.query(FactMarketDaily).filter_by(asset_id=asset.asset_id, trade_date=trade_date).first()
    if row is None:
        row = FactMarketDaily(asset_id=asset.asset_id, trade_date=trade_date)
        db.add(row)
    row.open = output.get("stck_oprc")
    row.high = output.get("stck_hgpr")
    row.low = output.get("stck_lwpr")
    row.close = output.get("stck_prpr")
    row.adj_close = output.get("stck_prpr")
    row.volume = output.get("acml_vol")
    row.source = "kis"


def run() -> None:
    db = SessionLocal()
    try:
        with track_ingestion_run(db, "kis_korean_equity_prices") as ingestion:
            prices = asyncio.run(_fetch_all_prices(list(SYMBOLS.keys())))
            trade_date = datetime.now(timezone.utc).date()

            for code, output in prices.items():
                asset = _get_or_create_asset(db, code, SYMBOLS[code])
                _upsert_price(db, asset, trade_date, output)

            db.commit()
            ingestion.raw_archive_path = "data/raw_archive/kis"
    finally:
        db.close()
