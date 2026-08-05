"""FRED에서 미국 주택가격 지표 5년 히스토리를 백필한다.

PCA-Ridge 예측 모델(global_rate_model.py)에 주택 지표를 학습 입력으로 추가하려면
다른 지표들과 마찬가지로 GIPS 5년 요건에 맞춰 최소 5년치 이력이 필요하다 —
backfill_macro_indicators.py와 동일한 구조(point-in-time 정확한 knowledge_date).
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy.orm import Session

from app.db.base import SessionLocal
from app.db.models.fact_market_daily import FactMarketDaily
from app.ingestion.connectors.fred_client import fetch_series_observations_with_first_publication_date
from app.ingestion.jobs.ingest_housing_indicators import ALL_SERIES, _get_or_create_asset
from app.ingestion.run_tracker import track_ingestion_run

BACKFILL_YEARS = 5


async def _fetch_all_series(start: str, end: str) -> dict[str, list[dict]]:
    async with httpx.AsyncClient() as client:
        results = {}
        for code, series_id in ALL_SERIES.items():
            results[code] = await fetch_series_observations_with_first_publication_date(
                client, series_id, start=start, end=end
            )
        return results


def run() -> None:
    db = SessionLocal()
    try:
        with track_ingestion_run(db, "backfill_housing_indicators") as ingestion:
            today = datetime.now(timezone.utc).date()
            start = (today - timedelta(days=BACKFILL_YEARS * 365)).strftime("%Y-%m-%d")
            end = today.strftime("%Y-%m-%d")

            series_data = asyncio.run(_fetch_all_series(start, end))
            upserted_total = 0

            for code, rows in series_data.items():
                asset = _get_or_create_asset(db, code)

                for row in rows:
                    if row["value"] == ".":
                        continue
                    trade_date = datetime.strptime(row["date"], "%Y-%m-%d").date()
                    knowledge_date = datetime.strptime(row["realtime_start"], "%Y-%m-%d").date()
                    value = float(row["value"])

                    existing = (
                        db.query(FactMarketDaily)
                        .filter_by(asset_id=asset.asset_id, trade_date=trade_date)
                        .first()
                    )
                    if existing is None:
                        existing = FactMarketDaily(
                            asset_id=asset.asset_id, trade_date=trade_date, knowledge_date=knowledge_date,
                        )
                        db.add(existing)
                    existing.close = value
                    existing.adj_close = value
                    existing.knowledge_date = knowledge_date
                    existing.source = "fred_backfill"
                    upserted_total += 1
                db.commit()

            ingestion.raw_archive_path = f"data/raw_archive/fred (upserted={upserted_total} rows)"
    finally:
        db.close()
