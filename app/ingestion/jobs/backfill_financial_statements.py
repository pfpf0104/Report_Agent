"""DART에서 삼성전자·SK하이닉스의 과거 5개 사업연도 BPS를 백필한다.

GIPS 성과 공시가 최소 5년을 요구하므로, 밸류에이션의 BPS 입력도 여러 연도가
필요하다. 매일 "최신 연도"만 가져오는 ingest_financial_statements.py와 달리,
이 job은 과거 5개 사업연도를 순회한다.

knowledge_date: rcept_no(접수번호) 앞 8자리에서 실제 공시일을 뽑아 쓴다
(app/ingestion/connectors/dart_client.py의 extract_filing_date 참고, 2026-08
실측 확인 — 삼성전자/SK하이닉스 여러 연도에서 전부 상식적인 3월 공시일로
파싱됨). 이전에는 회계연도 말+90일(마이그레이션 c81f3a5e2d47과 동일 근사)을
썼는데, rcept_dt 필드 자체는 없어도 rcept_no로 정확한 날짜가 복원되는 걸
확인해 근사 대신 실제값으로 교체했다.

과거 연도를 오늘 백필한다고 knowledge_date=오늘을 쓰면 안 된다는 점은 여전히
중요하다 — 예를 들어 2022 사업보고서(실제 공시 2023-03)를 2026년에 백필하면서
knowledge_date=2026을 넣으면 "2023~2025년 시점 리포트는 이 BPS를 몰랐다"고
잘못 표시하게 된다. 실제 공시일을 쓰면 이 문제가 원천적으로 없다.

재개 가능성: DB에 이미 있는 (asset, fiscal_year)는 API 호출 자체를 건너뛴다.
다른 백필 job들(backfill_macro_rates.py 등)은 이미 이렇게 동작하는데, 이
파일은 처음에 매 실행마다 5개년 x 2개사 전부를 무조건 다시 조회해 재실행 시
불필요한 DART API 쿼터를 소모했다 — 재무제표는 특히 회사당 요청 수가 적어서
비용이 커 보이지 않지만, 스케줄러가 이 job을 반복 실행하거나 여러 자산으로
확장되면 누적된다.
"""
from __future__ import annotations

import asyncio
from datetime import date, datetime, timezone

import httpx
from sqlalchemy.orm import Session

from app.db.base import SessionLocal
from app.db.models.dim_asset import AssetType, DimAsset
from app.db.models.fact_financial_quarterly import FactFinancialQuarterly
from app.ingestion.connectors.dart_client import (
    DartApiError,
    extract_capital_total,
    extract_filing_date,
    fetch_corp_code_map,
    fetch_single_company_financials,
)
from app.ingestion.jobs.ingest_financial_statements import SHARES_OUTSTANDING, STOCK_CODE
from app.ingestion.run_tracker import track_ingestion_run

BACKFILL_YEARS = 5


async def _fetch_bps_for_year(
    client: httpx.AsyncClient, corp_map: dict[str, str], name: str, bsns_year: int
) -> tuple[float, date] | None:
    """(BPS, 실제 공시일)을 반환한다. 실패하거나 필요한 필드가 없으면 None."""
    corp_code = corp_map.get(name)
    if corp_code is None:
        return None
    try:
        accounts = await fetch_single_company_financials(client, corp_code, bsns_year)
    except DartApiError:
        return None
    capital_total = extract_capital_total(accounts)
    filing_date = extract_filing_date(accounts)
    if capital_total is None or filing_date is None:
        return None
    return capital_total / SHARES_OUTSTANDING[name], filing_date


def _get_or_create_asset(db: Session, name: str) -> DimAsset:
    code = STOCK_CODE[name]
    asset = db.query(DimAsset).filter_by(code=code).first()
    if asset is None:
        asset = DimAsset(asset_type=AssetType.EQUITY.value, code=code, name_kr=name, currency="KRW")
        db.add(asset)
        db.commit()
        db.refresh(asset)
    return asset


def _existing_fiscal_years(db: Session, asset_id: int) -> set[int]:
    rows = db.query(FactFinancialQuarterly.fiscal_year).filter_by(asset_id=asset_id, fiscal_quarter=4).all()
    return {r[0] for r in rows}


def run() -> None:
    db = SessionLocal()
    try:
        with track_ingestion_run(db, "backfill_financial_statements") as ingestion:
            current_year = datetime.now(timezone.utc).year
            # 아직 공시 안 됐을 올해는 제외하고, 직전 5개 사업연도를 대상으로 한다.
            all_years = list(range(current_year - BACKFILL_YEARS, current_year))
            inserted_total = 0

            # 회사별로 이미 DB에 있는 연도는 API 호출 대상에서 뺀다(재개 가능성).
            assets_by_name = {name: _get_or_create_asset(db, name) for name in SHARES_OUTSTANDING}
            years_to_fetch_by_name = {
                name: [y for y in all_years if y not in _existing_fiscal_years(db, asset.asset_id)]
                for name, asset in assets_by_name.items()
            }

            async def _fetch_all():
                async with httpx.AsyncClient() as client:
                    corp_map = await fetch_corp_code_map(client)
                    results: dict[str, dict[int, tuple[float, date] | None]] = {}
                    for name, years in years_to_fetch_by_name.items():
                        results[name] = {
                            year: await _fetch_bps_for_year(client, corp_map, name, year) for year in years
                        }
                    return results

            if any(years_to_fetch_by_name.values()):
                bps_by_name = asyncio.run(_fetch_all())
            else:
                bps_by_name = {name: {} for name in SHARES_OUTSTANDING}

            for name, results_by_year in bps_by_name.items():
                asset = assets_by_name[name]
                for year, result in results_by_year.items():
                    if result is None:
                        continue
                    bps, knowledge_date = result
                    row = FactFinancialQuarterly(
                        asset_id=asset.asset_id,
                        fiscal_year=year,
                        fiscal_quarter=4,
                        knowledge_date=knowledge_date,
                        bps=bps,
                        source="dart_backfill",
                    )
                    db.add(row)
                    inserted_total += 1
                db.commit()

            ingestion.raw_archive_path = f"data/raw_archive/dart (inserted={inserted_total} rows)"
    finally:
        db.close()
