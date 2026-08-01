"""Yahoo Finance에서 XLE/SPY 5년 일별 시세를 백필한다.

GIPS 성과 공시(app/computation/risk/gips.py)가 최소 5년을 요구하므로,
CallRank의 벤치마크(XLE, SPY)도 최소 5년치가 필요하다. Yahoo Finance
chart API는 range_="5y"로 한 번에 전체 구간을 반환해 BOK/DART처럼 연도별
배치로 나눌 필요가 없다(app/ingestion/connectors/yahoo_finance_client.py).

knowledge_date = trade_date. 일별 시세는 당일 공표되는 정보라 사건일에
이미 알 수 있었다는 근사가 정확하다(ingest_equity_prices.py와 동일 규약).

이미 적재된 (asset_id, trade_date)는 건너뛴다(재개 가능).
"""
from __future__ import annotations

import asyncio
from datetime import date, datetime, timezone

import httpx
from sqlalchemy.orm import Session

from app.db.base import SessionLocal
from app.db.models.dim_asset import AssetType, DimAsset
from app.db.models.fact_market_daily import FactMarketDaily
from app.ingestion.connectors.yahoo_finance_client import fetch_daily_history
from app.ingestion.jobs.ingest_equity_prices import SYMBOLS
from app.ingestion.run_tracker import track_ingestion_run


def _get_or_create_asset(db: Session, symbol: str) -> DimAsset:
    asset = db.query(DimAsset).filter_by(code=symbol).first()
    if asset is None:
        asset = DimAsset(asset_type=AssetType.ETF.value, code=symbol, name_kr=symbol, currency="USD")
        db.add(asset)
        db.commit()
        db.refresh(asset)
    return asset


def _existing_trade_dates(db: Session, asset_id: int) -> set[date]:
    rows = db.query(FactMarketDaily.trade_date).filter_by(asset_id=asset_id).all()
    return {r[0] for r in rows}


async def _fetch_all_histories(symbols: list[str]) -> dict[str, list[dict]]:
    async with httpx.AsyncClient() as client:
        results = {}
        for symbol in symbols:
            results[symbol] = await fetch_daily_history(client, symbol, range_="5y")
        return results


def run() -> None:
    db = SessionLocal()
    try:
        with track_ingestion_run(db, "backfill_equity_prices") as ingestion:
            histories = asyncio.run(_fetch_all_histories(SYMBOLS))
            inserted_total = 0

            for symbol, rows in histories.items():
                asset = _get_or_create_asset(db, symbol)
                existing = _existing_trade_dates(db, asset.asset_id)

                for row in rows:
                    trade_date = datetime.fromtimestamp(row["timestamp"], tz=timezone.utc).date()
                    if trade_date in existing:
                        continue  # 재개 가능성: 이미 있는 날짜는 건너뛴다
                    db.add(
                        FactMarketDaily(
                            asset_id=asset.asset_id,
                            trade_date=trade_date,
                            knowledge_date=trade_date,  # 일별 시세는 당일 공표(위 docstring 참고)
                            open=row.get("open"),
                            high=row.get("high"),
                            low=row.get("low"),
                            close=row.get("close"),
                            adj_close=row.get("close"),
                            volume=row.get("volume"),
                            source="yahoo_finance_backfill",
                        )
                    )
                    existing.add(trade_date)
                    inserted_total += 1
                db.commit()

            ingestion.raw_archive_path = f"data/raw_archive/yahoo_finance (inserted={inserted_total} rows)"
    finally:
        db.close()
