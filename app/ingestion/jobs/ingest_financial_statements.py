"""DART에서 삼성전자·SK하이닉스의 최신 사업보고서 자본총계를 가져와 BPS(주당순자산
가치)를 계산해 fact_financial_quarterly에 적재하는 배치.

residual_income_model.py의 SAMSUNG_BOOK_VALUE/SK_HYNIX_BOOK_VALUE 하드코딩을
대체할 실데이터 소스다(대체는 이 배치가 자동으로 하지 않는다 — book_value_0을
이 테이블 조회로 바꾸는 건 residual_income_model.py 쪽에서 폴백과 함께 처리한다).

BPS = 자본총계(지배기업 소유주지분, DART 최신 사업보고서에서 매번 실제로 조회) /
발행주식총수. 자본총계는 시점마다 바뀌는 값이라 DART에서 자동으로 가져오지만,
발행주식총수는 이 세션이 확인한 DART 전용 API가 없어 상수로 고정한다 — 주식분할·
자사주소각 등 저빈도 이벤트로만 바뀌므로 상수로 둬도 위험이 작지만, 그런 이벤트가
있으면 SHARES_OUTSTANDING을 수동으로 갱신해야 한다.

TODO(확인 필요): SHARES_OUTSTANDING 값은 공개적으로 알려진 발행주식총수를 그대로
썼다 — 이 세션은 네트워크가 막혀 있어 최신 값으로 재검증하지 못했다. 로컬 세션에서
DART 사업보고서 원문이나 KRX 정보로 재확인할 것.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import httpx
from sqlalchemy.orm import Session

from app.db.base import SessionLocal
from app.db.models.dim_asset import AssetType, DimAsset
from app.db.models.fact_financial_quarterly import FactFinancialQuarterly
from app.ingestion.connectors.dart_client import (
    DartApiError,
    extract_capital_total,
    fetch_corp_code_map,
    fetch_single_company_financials,
)
from app.ingestion.run_tracker import track_ingestion_run

# 발행주식총수(보통주 기준). 분할·자사주소각 등 저빈도 이벤트로만 바뀐다 —
# TODO(확인 필요) 참고.
SHARES_OUTSTANDING = {
    "삼성전자": 5_969_782_550,
    "SK하이닉스": 728_002_365,
}
STOCK_CODE = {
    "삼성전자": "005930",
    "SK하이닉스": "000660",
}


async def _fetch_bps_for(
    client: httpx.AsyncClient, corp_map: dict[str, str], name: str, bsns_year: int
) -> float | None:
    corp_code = corp_map.get(name)
    if corp_code is None:
        return None
    try:
        accounts = await fetch_single_company_financials(client, corp_code, bsns_year)
    except DartApiError:
        # 해당 연도 사업보고서가 아직 공시되지 않았을 수 있다(예: as_of가 연초라 전년도
        # 보고서만 나와 있는 경우) — 호출부가 이전 연도로 재시도한다.
        return None
    capital_total = extract_capital_total(accounts)
    if capital_total is None:
        return None
    return capital_total / SHARES_OUTSTANDING[name]


async def _fetch_all_bps(bsns_year: int) -> tuple[dict[str, float | None], int]:
    """bsns_year 사업보고서가 없으면 1년 전으로 한 번 더 시도한다."""
    async with httpx.AsyncClient() as client:
        corp_map = await fetch_corp_code_map(client)
        for year in (bsns_year, bsns_year - 1):
            results = {name: await _fetch_bps_for(client, corp_map, name, year) for name in SHARES_OUTSTANDING}
            if any(v is not None for v in results.values()):
                return results, year
        return results, year


def _get_or_create_asset(db: Session, name: str) -> DimAsset:
    code = STOCK_CODE[name]
    asset = db.query(DimAsset).filter_by(code=code).first()
    if asset is None:
        asset = DimAsset(asset_type=AssetType.EQUITY.value, code=code, name_kr=name, currency="KRW")
        db.add(asset)
        db.commit()
        db.refresh(asset)
    return asset


def run(bsns_year: int | None = None) -> None:
    """bsns_year 생략 시 작년도 사업보고서(reprt_code=11011, 연간)를 조회한다."""
    db = SessionLocal()
    try:
        with track_ingestion_run(db, "dart_financial_statements") as ingestion:
            target_year = bsns_year or (datetime.now(timezone.utc).year - 1)
            bps_by_name, resolved_year = asyncio.run(_fetch_all_bps(target_year))

            for name, bps in bps_by_name.items():
                if bps is None:
                    continue
                asset = _get_or_create_asset(db, name)
                row = (
                    db.query(FactFinancialQuarterly)
                    .filter_by(asset_id=asset.asset_id, fiscal_year=resolved_year, fiscal_quarter=4)
                    .first()
                )
                if row is None:
                    row = FactFinancialQuarterly(asset_id=asset.asset_id, fiscal_year=resolved_year, fiscal_quarter=4)
                    db.add(row)
                row.bps = bps
                row.source = "dart"

            db.commit()
            ingestion.raw_archive_path = "data/raw_archive/dart"
    finally:
        db.close()
