"""FRED에서 미국 주택가격 지표를 가져와 fact_market_daily에 적재한다.

MetroGuard-KR의 City AI 원본 입력은 "미국 전국 주택 3개·미국 도시 주택 6개"
(city_ai_stub.py docstring)라고 하지만, 원본 보고서 PDF가 이 저장소에 없어
정확히 어떤 9개 시리즈였는지 알 수 없다. ingest_global_rates.py가 "글로벌
금리 21개"를 FRED에서 실측 확인 가능한 16개로 대체한 것과 같은 방식으로,
여기서는 FRED에 실제로 존재하는 대표 주택가격 지수로 "미국 주택시장"이라는
같은 취지의 실측 데이터를 채운다 — 원본과 시리즈가 정확히 일치하지는 않는다.

전국 3개: S&P/Case-Shiller 전국지수, FHFA 전미지수, 신규주택 판매중위가격.
도시 6개: S&P/Case-Shiller 20-City Composite을 구성하는 20개 대도시권 중
거래대금 상위 6곳(로스앤젤레스·뉴욕·샌프란시스코·시카고·마이애미·보스턴).

## Point-in-time: 관측월 ≠ 발표일

ingest_macro_indicators.py와 동일한 이유로 fetch_series_observations_with_
first_publication_date를 쓴다 — Case-Shiller·FHFA 모두 관측월과 실제
공표일 사이에 약 2개월 지연이 있다(예: 1월 관측치가 3월 말에 공개).
knowledge_date를 관측월로 근사하면 look-ahead bias가 생긴다.

## 미검증 사항

이 세션은 네트워크가 막혀 있어 아래 FRED_SERIES_ID들이 실제로 200을
반환하는지, 관측치가 최신까지 채워져 있는지 라이브 검증하지 못했다.
네트워크가 열린 환경에서 이 job을 최초 실행할 때 반드시 확인해야 한다
(ingest_micron_financials.py와 같은 처지 — MASTER_PLAN.md 참고).
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

# 전국 주택가격 지표 3개 — 전부 월간 또는 분기 발표.
NATIONAL_HOUSING_SERIES: dict[str, str] = {
    "USHPINAT": "CSUSHPISA",  # S&P/Case-Shiller U.S. National Home Price Index(월간, NSA)
    "USHPIFHFA": "USSTHPI",  # FHFA All-Transactions House Price Index for the US(분기)
    "USNEWHOMEPRICE": "MSPUS",  # Median Sales Price of Houses Sold(분기)
}

# 도시별(대도시권) 주택가격 지표 6개 — S&P/Case-Shiller 20-City Composite 구성
# 도시 중 거래대금 상위 6곳. 전부 월간(NSA).
CITY_HOUSING_SERIES: dict[str, str] = {
    "USHPILA": "LXXRNSA",  # Los Angeles
    "USHPINY": "NYXRNSA",  # New York
    "USHPISF": "SFXRNSA",  # San Francisco
    "USHPICHI": "CHXRNSA",  # Chicago
    "USHPIMIA": "MIXRNSA",  # Miami
    "USHPIBOS": "BOXRNSA",  # Boston
}

ALL_SERIES: dict[str, str] = {**NATIONAL_HOUSING_SERIES, **CITY_HOUSING_SERIES}


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
        with track_ingestion_run(db, "fred_housing_indicators") as ingestion:
            today = datetime.now(timezone.utc).date()
            # 분기 지표(USHPIFHFA·USNEWHOMEPRICE)까지 놓치지 않으려면 넉넉히
            # 되짚어야 한다 — ingest_macro_indicators.py와 동일하게 2년.
            start = (today.replace(year=today.year - 2)).strftime("%Y-%m-%d")
            end = today.strftime("%Y-%m-%d")

            series_data = asyncio.run(_fetch_all_series(start, end))
            upserted_total = 0

            for code, rows in series_data.items():
                if not rows:
                    continue
                asset = _get_or_create_asset(db, code)

                for row in rows:
                    if row["value"] == ".":  # FRED 결측 마커
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
