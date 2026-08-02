"""FRED에서 거시경제 지표(GDP·CPI·산업생산·고용 등) 5년 히스토리를 백필한다.

레짐 분류기(app/computation/regime/classifier.py)가 YoY 변화율을 계산하려면
최소 2년+α(전년동월 비교) 이력이 필요하고, GIPS 5년 요건과 맞추기 위해
다른 백필 job들과 동일하게 5년치를 채운다.

FRED는 observation_start/end로 임의 기간을 한 번에 조회할 수 있어(2026-08
실측: 5.5년치 66개월 관측치를 단일 요청으로 수신) BOK ECOS 백필처럼 연도별로
나눌 필요가 없다 — backfill_global_rates.py와 같은 구조다.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy.orm import Session

from app.db.base import SessionLocal
from app.db.models.fact_market_daily import FactMarketDaily
from app.ingestion.connectors.fred_client import fetch_series_observations_with_first_publication_date
from app.ingestion.jobs.ingest_macro_indicators import ALL_SERIES, _get_or_create_asset
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
        with track_ingestion_run(db, "backfill_macro_indicators") as ingestion:
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
