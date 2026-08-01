"""BOK ECOS에서 국고채 1년/3년 금리 5년 히스토리를 백필한다.

GIPS 성과 공시(app/computation/risk/gips.py)가 최소 5년 연간 수익률을 요구하므로,
MetroGuard의 입력(KTB1Y/KTB3Y)도 최소 5년치가 필요하다. 매일 오늘자만 가져오는
ingest_macro_rates.py와 달리, 이 job은 과거 구간을 한 번에 채운다.

설계 원칙:
  - BOK StatisticSearch는 start_no~end_no로 응답 건수를 제한한다(문서상 최대치가
    명시돼 있지 않지만, 5년(약 1,250 영업일)을 한 번에 받으면 응답이 지나치게
    커질 수 있어 연도 단위로 나눠 호출한다 — 실패 시 해당 연도만 재시도하면 된다.
  - knowledge_date = trade_date. 일별 국고채 금리는 해당일에 바로 공표되는
    정보라 "사건일에 이미 알 수 있었다"는 근사가 정확하다(ingest_macro_rates.py와
    동일 규약, app/db/point_in_time.py 참고). 과거로 재구성한다고 knowledge_date를
    오늘로 채우면 "그 시점엔 몰랐던 정보"처럼 취급돼 point-in-time 정합성이 깨진다.
  - 이미 적재된 (asset_id, trade_date)는 건너뛴다(재개 가능) — upsert가 아니라
    "없는 것만 채우는" 방식이라 재실행해도 안전하다.
  - 단위는 ingest_macro_rates.py와 동일하게 bp로 정규화한다(BOK는 %를 준다,
    G13 규약 참고).
  - HTTP 호출은 단일 이벤트 루프·단일 AsyncClient로 전부 처리한다(_fetch_all_years).
    자산×연도 조합마다 asyncio.run()을 반복 호출하던 이전 버전은 매번 새 이벤트
    루프와 커넥션을 만들어 버려 다른 백필 job들과의 패턴 일관성도 깨져 있었다.
"""
from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta, timezone

import httpx
from sqlalchemy.orm import Session

from app.db.base import SessionLocal
from app.db.models.dim_asset import AssetType, DimAsset
from app.db.models.fact_market_daily import FactMarketDaily
from app.ingestion.connectors.bok_client import fetch_statistic_search
from app.ingestion.jobs.ingest_macro_rates import MACRO_SERIES, STAT_CODE
from app.ingestion.run_tracker import track_ingestion_run

BACKFILL_YEARS = 5


def _get_or_create_macro_asset(db: Session, code: str) -> DimAsset:
    asset = db.query(DimAsset).filter_by(code=code).first()
    if asset is None:
        asset = DimAsset(asset_type=AssetType.MACRO.value, code=code, name_kr=code, currency="KRW")
        db.add(asset)
        db.commit()
        db.refresh(asset)
    return asset


def _existing_trade_dates(db: Session, asset_id: int) -> set[date]:
    rows = db.query(FactMarketDaily.trade_date).filter_by(asset_id=asset_id).all()
    return {r[0] for r in rows}


async def _fetch_year(client: httpx.AsyncClient, item_code: str, year: int) -> list[dict]:
    start = f"{year}0101"
    today = datetime.now(timezone.utc).date()
    end_date = date(year, 12, 31) if year < today.year else today
    end = end_date.strftime("%Y%m%d")
    return await fetch_statistic_search(client, STAT_CODE, "D", start, end, item_code, start_no=1, end_no=400)


async def _fetch_all_years(codes: dict[str, str], years: list[int]) -> dict[str, dict[int, list[dict]]]:
    """다른 백필 job들과 동일 패턴 — 하나의 이벤트 루프·클라이언트로 전체를 가져온다.

    이전에는 (자산 x 연도) 조합마다 asyncio.run()을 따로 호출해 매번 새 이벤트
    루프와 httpx.AsyncClient를 만들고 버렸다(10회, 연결 재사용 없이 TCP 핸드셰이크
    반복) — 다른 백필 job들의 "_fetch_all() 한 번만 호출" 패턴과도 어긋났다.
    """
    async with httpx.AsyncClient() as client:
        results: dict[str, dict[int, list[dict]]] = {}
        for code, item_code in codes.items():
            results[code] = {year: await _fetch_year(client, item_code, year) for year in years}
        return results


def run() -> None:
    db = SessionLocal()
    try:
        with track_ingestion_run(db, "backfill_macro_rates") as ingestion:
            today = datetime.now(timezone.utc).date()
            years = list(range(today.year - BACKFILL_YEARS + 1, today.year + 1))
            inserted_total = 0

            rows_by_code = asyncio.run(_fetch_all_years(MACRO_SERIES, years))

            for code, rows_by_year in rows_by_code.items():
                asset = _get_or_create_macro_asset(db, code)
                existing = _existing_trade_dates(db, asset.asset_id)

                for rows in rows_by_year.values():
                    for row in rows:
                        trade_date = datetime.strptime(row["TIME"], "%Y%m%d").date()
                        if trade_date in existing:
                            continue  # 재개 가능성: 이미 있는 날짜는 건너뛴다
                        yield_bp = float(row["DATA_VALUE"]) * 100  # % -> bp (G13)
                        db.add(
                            FactMarketDaily(
                                asset_id=asset.asset_id,
                                trade_date=trade_date,
                                knowledge_date=trade_date,  # 일별 금리는 당일 공표(위 docstring 참고)
                                close=yield_bp,
                                adj_close=yield_bp,
                                source="bok_ecos_backfill",
                            )
                        )
                        existing.add(trade_date)
                        inserted_total += 1
                db.commit()

            ingestion.raw_archive_path = f"data/raw_archive/bok (inserted={inserted_total} rows, {len(years)}y)"
    finally:
        db.close()
