"""FRED에서 성장·인플레이션 거시경제 지표를 가져와 fact_market_daily에 적재한다.

Phase 3-1 레짐 분류기(app/computation/regime/classifier.py)의 입력이다.
판정 지표(산업생산=성장, CPI=인플레)와 보조 확인 지표(GDP·PCE·고용)를
구분한다 — 판정 로직은 REGIME_DECISION_SERIES 2개에만 의존하고, 나머지는
페이지에 참고용으로만 병기한다(설계 근거는 classifier.py docstring 참고).

## Point-in-time: 관측월 ≠ 발표일

BOK 국고채·FRED 금리곡선과 달리, 거시경제 지표는 관측월과 실제 발표일 사이에
몇 주 지연이 있다(2026-08 실측: FRED INDPRO 2026-01 관측치가 2026-02-18에
처음 공개됨 — 약 6주 지연). knowledge_date를 관측월로 잡으면 "그 시점엔
아직 발표되지 않은 수치를 그 시점에 알고 있었던 것"처럼 되어 look-ahead
bias가 생긴다. fred_client.fetch_series_observations_with_first_publication_date
가 FRED의 realtime vintage API로 각 관측치의 정확한 최초 공표일을 조회해
knowledge_date로 쓴다.

## 단위: 원단위 그대로 저장

MACRO(bp 정규화)와 달리 MACRO_ECONOMIC은 원단위 그대로 저장한다 — CPI/PCE는
지수 레벨(기준연도=100), 산업생산도 지수 레벨, GDP는 실질 10억달러 단위,
고용은 천명 단위다. bp로 정규화할 대상이 아니다(단위 자체가 서로 다르고
레짐 분류기는 YoY% 변화만 쓰므로 절대 단위가 통일될 필요가 없다).
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import httpx
from sqlalchemy.orm import Session

from app.db.base import SessionLocal
from app.db.models.dim_asset import AssetType, DimAsset
from app.db.models.fact_market_daily import FactMarketDaily
from app.ingestion.connectors.fred_client import fetch_series_observations_with_first_publication_date
from app.ingestion.run_tracker import track_ingestion_run

# 레짐 판정에 직접 쓰는 2개 — classifier.py가 이 코드만 참조한다.
REGIME_DECISION_SERIES: dict[str, str] = {
    "USINDPRO": "INDPRO",  # 산업생산지수(월간) — 성장 대리지표
    "USCPI": "CPIAUCSL",  # 소비자물가지수(월간, 계절조정) — 인플레 대리지표
}

# 판정에 관여하지 않는 보조 확인 지표 — 리포트에 참고용으로만 병기한다.
REGIME_REFERENCE_SERIES: dict[str, str] = {
    "USGDP": "GDPC1",  # 실질GDP(분기)
    "USPCE": "PCEPI",  # PCE 물가지수(월간)
    "USPAYEMS": "PAYEMS",  # 비농업고용(월간, 천명)
}

ALL_SERIES: dict[str, str] = {**REGIME_DECISION_SERIES, **REGIME_REFERENCE_SERIES}


async def _fetch_all_series(start: str, end: str) -> dict[str, list[dict]]:
    async with httpx.AsyncClient() as client:
        results = {}
        for code, series_id in ALL_SERIES.items():
            results[code] = await fetch_series_observations_with_first_publication_date(
                client, series_id, start=start, end=end
            )
        return results


def _get_or_create_asset(db: Session, code: str) -> DimAsset:
    asset = db.query(DimAsset).filter_by(code=code).first()
    if asset is None:
        asset = DimAsset(
            asset_type=AssetType.MACRO_ECONOMIC.value, code=code, name_kr=code, currency="USD",
        )
        db.add(asset)
        db.commit()
        db.refresh(asset)
    return asset


def run() -> None:
    db = SessionLocal()
    try:
        with track_ingestion_run(db, "fred_macro_indicators") as ingestion:
            today = datetime.now(timezone.utc).date()
            # 월간·분기 지표라 최근 관측치 갱신 빈도가 낮다 — 넉넉히 400일(약
            # 13개월)을 되짚어 지연 발표된 개정치까지 놓치지 않는다. 이미
            # 적재된 (asset_id, trade_date)는 그대로 갱신되므로(재개 가능)
            # 매일 실행해도 안전하다.
            start = (today.replace(year=today.year - 2)).strftime("%Y-%m-%d")
            end = today.strftime("%Y-%m-%d")

            series_data = asyncio.run(_fetch_all_series(start, end))
            upserted_total = 0

            for code, rows in series_data.items():
                if not rows:
                    continue
                asset = _get_or_create_asset(db, code)

                for row in rows:
                    if row["value"] == ".":  # FRED 결측 마커(아직 개정 전 등)
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
                    existing.source = "fred"
                    upserted_total += 1
                db.commit()

            ingestion.raw_archive_path = f"data/raw_archive/fred (upserted={upserted_total} rows)"
    finally:
        db.close()
