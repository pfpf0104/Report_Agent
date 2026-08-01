"""Yahoo Finance에서 삼성전자·SK하이닉스 5년 일별 종가를 백필한다.

KIS OpenAPI는 현재가 조회(inquire-price)만 제공하고 히스토리 API가 없어
백필에는 쓸 수 없다. 대신 Yahoo Finance가 ".KS" 접미사로 코스피 종목을
지원하며, 최신 종가가 KIS 실시간 시세와 정확히 일치함을 실측 확인했다
(app/ingestion/connectors/yahoo_finance_client.py docstring 참고). 매일
갱신되는 ingest_korean_equity_prices.py(KIS, source="kis")는 그대로 두고,
이 job만 과거 구간을 Yahoo(source="yahoo_finance_backfill")로 채운다 —
날짜가 겹치지 않으므로 두 소스가 공존해도 문제 없다.

knowledge_date = trade_date(일별 시세는 당일 공표, 다른 시세 job들과 동일 규약).
이미 적재된 (asset_id, trade_date)는 건너뛴다(재개 가능, KIS가 채운 최신
거래일과 자동으로 겹치지 않는다).
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
from app.ingestion.jobs.ingest_korean_equity_prices import SYMBOLS
from app.ingestion.run_tracker import track_ingestion_run

# Yahoo Finance 심볼 접미사(위 docstring 참고).
_YAHOO_SUFFIX = ".KS"


def _get_or_create_asset(db: Session, code: str, name_kr: str) -> DimAsset:
    asset = db.query(DimAsset).filter_by(code=code).first()
    if asset is None:
        asset = DimAsset(asset_type=AssetType.EQUITY.value, code=code, name_kr=name_kr, currency="KRW")
        db.add(asset)
        db.commit()
        db.refresh(asset)
    return asset


def _existing_trade_dates(db: Session, asset_id: int) -> set[date]:
    rows = db.query(FactMarketDaily.trade_date).filter_by(asset_id=asset_id).all()
    return {r[0] for r in rows}


async def _fetch_all_histories(codes: list[str]) -> dict[str, list[dict]]:
    async with httpx.AsyncClient() as client:
        results = {}
        for code in codes:
            results[code] = await fetch_daily_history(client, f"{code}{_YAHOO_SUFFIX}", range_="5y")
        return results


def run() -> None:
    db = SessionLocal()
    try:
        with track_ingestion_run(db, "backfill_korean_equity_prices") as ingestion:
            histories = asyncio.run(_fetch_all_histories(list(SYMBOLS.keys())))
            inserted_total = 0

            for code, rows in histories.items():
                asset = _get_or_create_asset(db, code, SYMBOLS[code])
                existing = _existing_trade_dates(db, asset.asset_id)

                for row in rows:
                    trade_date = datetime.fromtimestamp(row["timestamp"], tz=timezone.utc).date()
                    if trade_date in existing:
                        continue  # 재개 가능성 + KIS가 이미 채운 최신 거래일과 자동 회피
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
