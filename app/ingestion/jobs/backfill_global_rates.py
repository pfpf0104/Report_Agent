"""FRED에서 미국 금리곡선·매크로 지표 5년 히스토리를 백필한다.

PCA-Ridge 예측 모델(app/computation/fixed_income/global_rate_model.py)이
60개월 워크포워드 학습창을 요구하므로(MetroGuard-KR 보고서 7페이지), 최소
5년치가 필요하다 — GIPS 5년 요건과 같은 이유다.

BOK ECOS(backfill_macro_rates.py)와 달리 FRED는 observation_start/end로
임의 기간을 한 번에 조회할 수 있어(2026-08 실측: 5년치 1,455개 관측치를
단일 요청으로 수신) 연도별로 나눠 호출할 필요가 없다 — 그만큼 이 job은
BOK 백필보다 단순하다.
"""
from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta, timezone

import httpx
from sqlalchemy.orm import Session

from app.db.base import SessionLocal
from app.db.models.fact_market_daily import FactMarketDaily
from app.ingestion.connectors.fred_client import fetch_series_observations
from app.ingestion.jobs.ingest_global_rates import ALL_SERIES, GLOBAL_RATE_SERIES, _get_or_create_asset
from app.ingestion.run_tracker import track_ingestion_run

BACKFILL_YEARS = 5


async def _fetch_all_series(start: str, end: str) -> dict[str, list[dict]]:
    async with httpx.AsyncClient() as client:
        results = {}
        for code, series_id in ALL_SERIES.items():
            results[code] = await fetch_series_observations(
                client, series_id, start=start, end=end, limit=100_000
            )
        return results


def _existing_trade_dates(db: Session, asset_id: int) -> set[date]:
    rows = db.query(FactMarketDaily.trade_date).filter_by(asset_id=asset_id).all()
    return {r[0] for r in rows}


def run() -> None:
    db = SessionLocal()
    try:
        with track_ingestion_run(db, "backfill_global_rates") as ingestion:
            today = datetime.now(timezone.utc).date()
            start = (today - timedelta(days=BACKFILL_YEARS * 365)).strftime("%Y-%m-%d")
            end = today.strftime("%Y-%m-%d")

            series_data = asyncio.run(_fetch_all_series(start, end))
            inserted_total = 0

            for code, rows in series_data.items():
                asset = _get_or_create_asset(db, code)
                existing = _existing_trade_dates(db, asset.asset_id)

                for row in rows:
                    if row["value"] == ".":  # FRED 결측일(공휴일 등) 표기
                        continue
                    trade_date = datetime.strptime(row["date"], "%Y-%m-%d").date()
                    if trade_date in existing:
                        continue  # 재개 가능성: 이미 있는 날짜는 건너뛴다
                    raw_value = float(row["value"])
                    value = raw_value * 100 if code in GLOBAL_RATE_SERIES else raw_value
                    db.add(
                        FactMarketDaily(
                            asset_id=asset.asset_id,
                            trade_date=trade_date,
                            knowledge_date=trade_date,  # ingest_global_rates.py와 동일 규약
                            close=value,
                            adj_close=value,
                            source="fred_backfill",
                        )
                    )
                    existing.add(trade_date)
                    inserted_total += 1
                db.commit()

            ingestion.raw_archive_path = f"data/raw_archive/fred (inserted={inserted_total} rows)"
    finally:
        db.close()
