"""BOK ECOS에서 한국 국고채 금리를 가져와 fact_market_daily(매크로 자산)에 적재하는 배치.

MetroGuard의 carry-price gate가 쓰는 Y(1), Y(3) 실제 금리 입력이다.

실측 완료: STAT_CODE="817Y002"(시장금리, 일별), ITEM_CODE KTB1Y="010190000"
(국고채 1년), KTB3Y="010200000"(국고채 3년) — 로컬 PC에서 실제 API 키로 조회해
2025-01-02 기준 각각 2.659%, 2.507%로 정상 응답 확인함(2026-08 검증).
이전 값(STAT_CODE="722Y001"=기준금리, ITEM_CODE="0101000"/"0101002")은 잘못된
통계와 존재하지 않는 품목코드였다.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import httpx
from sqlalchemy.orm import Session

from app.db.base import SessionLocal
from app.db.models.dim_asset import AssetType, DimAsset
from app.db.models.fact_market_daily import FactMarketDaily
from app.ingestion.connectors.bok_client import fetch_statistic_search
from app.ingestion.run_tracker import track_ingestion_run

STAT_CODE = "817Y002"  # 시장금리(일별) — 실측 완료
CYCLE = "D"

# 국고채 1년/3년 품목코드 — 실측 완료(2026-08, 위 docstring 참고).
MACRO_SERIES = {
    "KTB1Y": "010190000",
    "KTB3Y": "010200000",
}


async def _fetch_all_series(start: str, end: str) -> dict[str, list[dict]]:
    async with httpx.AsyncClient() as client:
        results = {}
        for code, item_code in MACRO_SERIES.items():
            results[code] = await fetch_statistic_search(client, STAT_CODE, CYCLE, start, end, item_code)
        return results


def _get_or_create_macro_asset(db: Session, code: str) -> DimAsset:
    asset = db.query(DimAsset).filter_by(code=code).first()
    if asset is None:
        asset = DimAsset(asset_type=AssetType.MACRO.value, code=code, name_kr=code, currency="KRW")
        db.add(asset)
        db.commit()
        db.refresh(asset)
    return asset


def run() -> None:
    db = SessionLocal()
    try:
        with track_ingestion_run(db, "bok_macro_rates") as ingestion:
            today = datetime.now(timezone.utc).date()
            start = today.replace(day=1).strftime("%Y%m%d")
            end = today.strftime("%Y%m%d")

            series_data = asyncio.run(_fetch_all_series(start, end))

            for code, rows in series_data.items():
                if not rows:
                    continue
                asset = _get_or_create_macro_asset(db, code)
                # ECOS 응답의 정렬 순서를 신뢰하지 않고 TIME 값으로 직접 최신 행을 고른다
                # (정렬 순서는 이 세션에서 실제 응답으로 확인하지 못한 가정이었다).
                latest = max(rows, key=lambda r: r["TIME"])
                trade_date = datetime.strptime(latest["TIME"], "%Y%m%d").date()
                yield_value = float(latest["DATA_VALUE"])

                row = (
                    db.query(FactMarketDaily)
                    .filter_by(asset_id=asset.asset_id, trade_date=trade_date)
                    .first()
                )
                if row is None:
                    row = FactMarketDaily(asset_id=asset.asset_id, trade_date=trade_date)
                    db.add(row)
                row.close = yield_value
                row.adj_close = yield_value
                row.source = "bok_ecos"

            db.commit()
            ingestion.raw_archive_path = "data/raw_archive/bok"
    finally:
        db.close()
