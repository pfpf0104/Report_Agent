"""FRED(세인트루이스 연준)에서 미국 금리곡선·매크로 지표를 가져와 fact_market_daily에 적재한다.

City AI의 "글로벌 금리 21개" 입력(MetroGuard-KR 보고서 7페이지)을 재현하려면
원본이 정확히 어떤 21개 시리즈를 썼는지가 필요한데, 그 보고서 원문이 저장소에
없어 알 수 없다(city_ai_stub.py TODO 참고). 대신 여기서는 FRED에서 실측 확인한
16개 시리즈(미국 국채 11개 만기 + 정책금리 + 금리곡선 스프레드 2개 + 신용
스프레드 + 달러지수)로 구성한다 — "원본과 정확히 일치"는 아니지만 "한국
금리에 영향을 주는 미국/글로벌 금리 환경"이라는 취지는 같은 방향의 실측
데이터다.

## 단위 규약 (G13과 동일 원칙, USD 버전)

GLOBAL_RATE_SERIES(11개 만기+정책금리, 총 12개)는 FRED가 퍼센트로 주는 값을
×100 해서 bp로 저장한다 — KTB1Y/KTB3Y와 같은 (MACRO, bp) 규약을 그대로
따른다. 다른 통화(USD)일 뿐 "금리"라는 성격은 같기 때문이다.

GLOBAL_INDEX_SERIES(스프레드 2개+신용스프레드+달러지수, 총 4개)는 원단위
그대로 저장한다 — T10Y2Y는 금리곡선 역전 시 음수가 되는 %p 값이고 DTWEXBGS는
100 안팎의 무차원 지수라 bp로 정규화하는 게 의미가 없다. asset_type을
MACRO_INDEX로 분리해 quality.py 상식범위도 따로 둔다(app/db/models/dim_asset.py
AssetType docstring 참고).

## FRED 특유의 결측일 처리

FRED 일별 시리즈는 미국 공휴일에 결측이고, 관측치 자체가 며칠 늦게 개정
공표되는 경우가 있다(BOK와 다른 점 — BOK는 당일 공표, FRED 국채수익률은
당일 발표되지만 시리즈에 따라 공표 지연이 있을 수 있다). ingest_macro_rates.py
와 동일하게 넉넉한 트레일링 윈도우로 조회해 최신 관측치만 골라 쓴다.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy.orm import Session

from app.db.base import SessionLocal
from app.db.models.dim_asset import AssetType, DimAsset
from app.db.models.fact_market_daily import FactMarketDaily
from app.ingestion.connectors.fred_client import fetch_series_observations
from app.ingestion.run_tracker import track_ingestion_run

# 미국 국채 11개 만기 + 정책금리(DFF, 일별) — bp로 정규화해 저장한다.
# 실측 확인(2026-08): 전부 200 응답, 최신 관측치 존재.
GLOBAL_RATE_SERIES: dict[str, str] = {
    "US1MO": "DGS1MO",
    "US3MO": "DGS3MO",
    "US6MO": "DGS6MO",
    "US1Y": "DGS1",
    "US2Y": "DGS2",
    "US3Y": "DGS3",
    "US5Y": "DGS5",
    "US7Y": "DGS7",
    "US10Y": "DGS10",
    "US20Y": "DGS20",
    "US30Y": "DGS30",
    "USFEDFUNDS": "DFF",
}

# 스프레드·지수 — 원단위(%p 또는 무차원 지수) 그대로 저장한다.
GLOBAL_INDEX_SERIES: dict[str, str] = {
    "US10Y2Y": "T10Y2Y",  # 10년-2년 스프레드(%p) — 역전 시 음수
    "US10Y3M": "T10Y3M",  # 10년-3개월 스프레드(%p) — 역전 시 음수
    "USHYSPREAD": "BAMLH0A0HYM2",  # ICE BofA 하이일드 OAS(%p)
    "USDINDEX": "DTWEXBGS",  # 연준 광의 달러지수(무차원)
}

ALL_SERIES: dict[str, str] = {**GLOBAL_RATE_SERIES, **GLOBAL_INDEX_SERIES}


async def _fetch_all_series(start: str, end: str) -> dict[str, list[dict]]:
    async with httpx.AsyncClient() as client:
        results = {}
        for code, series_id in ALL_SERIES.items():
            results[code] = await fetch_series_observations(
                client, series_id, start=start, end=end, limit=30
            )
        return results


def _get_or_create_asset(db: Session, code: str) -> DimAsset:
    asset = db.query(DimAsset).filter_by(code=code).first()
    if asset is None:
        asset_type = (
            AssetType.MACRO.value if code in GLOBAL_RATE_SERIES else AssetType.MACRO_INDEX.value
        )
        asset = DimAsset(asset_type=asset_type, code=code, name_kr=code, currency="USD")
        db.add(asset)
        db.commit()
        db.refresh(asset)
    return asset


def _latest_non_null_observation(rows: list[dict]) -> dict | None:
    """FRED는 결측일을 value="." 문자열로 채워 넣는다(공휴일 등) — 이를
    건너뛰고 실제 값이 있는 가장 최근 관측치를 고른다."""
    for row in sorted(rows, key=lambda r: r["date"], reverse=True):
        if row["value"] != ".":
            return row
    return None


def run() -> None:
    db = SessionLocal()
    try:
        with track_ingestion_run(db, "fred_global_rates") as ingestion:
            today = datetime.now(timezone.utc).date()
            # 미국 공휴일·주말·개정공표 지연을 감안해 넉넉히 15일 되짚는다
            # (ingest_macro_rates.py의 10일 윈도우보다 여유를 더 둔다 — FRED
            # 일부 시리즈는 BOK보다 공표가 며칠 늦을 수 있다).
            start = (today - timedelta(days=15)).strftime("%Y-%m-%d")
            end = today.strftime("%Y-%m-%d")

            series_data = asyncio.run(_fetch_all_series(start, end))

            for code, rows in series_data.items():
                latest = _latest_non_null_observation(rows)
                if latest is None:
                    continue
                asset = _get_or_create_asset(db, code)
                trade_date = datetime.strptime(latest["date"], "%Y-%m-%d").date()
                raw_value = float(latest["value"])
                # 금리류만 bp로 정규화(위 docstring 참고) — 스프레드/지수는 원단위.
                value = raw_value * 100 if code in GLOBAL_RATE_SERIES else raw_value

                row = (
                    db.query(FactMarketDaily)
                    .filter_by(asset_id=asset.asset_id, trade_date=trade_date)
                    .first()
                )
                if row is None:
                    # FRED 일별 시리즈는 해당일 장마감 기준으로 당일 공표되므로
                    # knowledge_date = trade_date(다른 매크로 job들과 동일 규약).
                    row = FactMarketDaily(
                        asset_id=asset.asset_id, trade_date=trade_date, knowledge_date=trade_date
                    )
                    db.add(row)
                row.close = value
                row.adj_close = value
                row.source = "fred"

            db.commit()
            ingestion.raw_archive_path = "data/raw_archive/fred"
    finally:
        db.close()
