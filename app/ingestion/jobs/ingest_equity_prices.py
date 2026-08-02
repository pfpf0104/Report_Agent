"""Yahoo Finance에서 ETF/주식 시세를 가져와 fact_market_daily에 적재하는 배치.

이전에는 FMP를 썼으나, FMP 무료 플랜이 XLE/QQQ 같은 일부 ETF를 402(구독 필요)로
막아 CallRank가 실제로 쓰는 XLE 조회가 불가능해졌다(2026-08 실측). Yahoo Finance
비공식 차트 API(무료, 키 불필요)로 교체했다 — app/ingestion/connectors/
yahoo_finance_client.py 참고.

SYMBOLS = CallRank의 11개 섹터 ETF 전체 + 벤치마크(SPY). 이전에는 XLE 하나만
다뤄 risk/report_context.py의 성과 페이지가 "유니버스 자산 1개 — 2개 이상
필요"로 항상 보류됐다 — 리스크패리티 배분은 최소 2개 자산의 공분산이 필요한데
섹터 ETF 실데이터가 사실상 하나뿐이었기 때문이다. SECTOR_ETF_BY_NAME에서
동적으로 가져와 하드코딩 목록을 이중 관리하지 않는다.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import httpx
from sqlalchemy.orm import Session

from app.computation.quant.sector_embeddings import SECTOR_ETF_BY_NAME
from app.db.base import SessionLocal
from app.db.models.dim_asset import AssetType, DimAsset
from app.db.models.fact_market_daily import FactMarketDaily
from app.ingestion.connectors.yahoo_finance_client import fetch_daily_history
from app.ingestion.run_tracker import track_ingestion_run

SYMBOLS = sorted(set(SECTOR_ETF_BY_NAME.values()) | {"SPY"})


async def _fetch_all_quotes(symbols: list[str]) -> dict[str, dict]:
    async with httpx.AsyncClient() as client:
        results = {}
        for symbol in symbols:
            history = await fetch_daily_history(client, symbol, range_="5d")
            if history:
                results[symbol] = history[-1]  # 가장 최근 거래일
        return results


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
    row.high = quote.get("high")
    row.low = quote.get("low")
    row.close = quote.get("close")
    row.adj_close = quote.get("close")
    row.volume = quote.get("volume")
    row.source = "yahoo_finance"


def run() -> None:
    db = SessionLocal()
    try:
        with track_ingestion_run(db, "yahoo_equity_prices") as ingestion:
            quotes = asyncio.run(_fetch_all_quotes(SYMBOLS))
            trade_date = datetime.now(timezone.utc).date()

            for symbol, quote in quotes.items():
                asset = _get_or_create_asset(db, symbol)
                _upsert_quote(db, asset, trade_date, quote)

            db.commit()
            ingestion.raw_archive_path = "data/raw_archive/yahoo_finance"
    finally:
        db.close()
