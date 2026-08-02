"""KIS에서 국내 주식·채권 ETF 현재가를 가져와 fact_market_daily에 적재한다.

SYMBOLS에는 두 그룹이 섞여 있다:
  - 삼성전자·SK하이닉스: 밸류에이션 리포트(residual_income_model.py)의
    CURRENT_PRICE 하드코딩을 대체할 실데이터 소스다 — 다만 이 배치만으로는
    자동 대체되지 않고, CURRENT_PRICE를 이 테이블 조회로 바꾸는 건 별도 작업이다.
  - KOSEF/KODEX류 국채 ETF: MetroGuard(app/computation/fixed_income/
    duration_controller.py)의 D1/D3 인덱스 전환 전략을 백테스트하기 위한
    실제 거래 가능 대리자산이다.

    한국 시장에는 순수 "국고채 1년" 만기 ETF가 상장돼 있지 않다(국고채는
    3/10/30년만 있다) — 2026-08 실측: 네이버 금융 ETF 전체 목록(1155개)을
    훑어 확인했다. 1년물 지표금리는 통안채(한국은행 발행, 국고채와 발행주체가
    다르다)로만 ETF화돼 있다. KTB1Y 통계치(BOK ECOS 817Y002/010190000) 자체가
    시장에서 이런 구조로 형성되므로, MetroGuard의 "1년물" 대리자산으로 국고채가
    아닌 통안채 ETF를 쓴다 — 신용 리스크(국채 vs 중앙은행채)는 사실상 없지만
    발행주체가 다르다는 점을 리포트에 명시해야 한다.

    122260(KIWOOM 통안채1년)·114260(KODEX 국고채3년) 둘 다 KIS 실시간 시세와
    Yahoo Finance 5년 이력이 실측 확인됐다(app/ingestion/connectors/
    yahoo_finance_client.py 참고).
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import httpx
from sqlalchemy.orm import Session

from app.db.base import SessionLocal
from app.db.models.dim_asset import AssetType, DimAsset
from app.db.models.fact_market_daily import FactMarketDaily
from app.ingestion.connectors.kis_client import fetch_stock_price
from app.ingestion.run_tracker import track_ingestion_run

# code: 한글명
SYMBOLS = {
    "005930": "삼성전자",
    "000660": "SK하이닉스",
    "122260": "KIWOOM 통안채1년",
    "114260": "KODEX 국고채3년",
}

# MetroGuard 백테스트가 참조하는 채권 ETF 코드(app/computation/risk/report_context.py).
BOND_ETF_SHORT = "122260"  # 1년물 대리자산(통안채)
BOND_ETF_LONG = "114260"  # 3년물 대리자산(국고채)

_BOND_ETF_CODES = {BOND_ETF_SHORT, BOND_ETF_LONG}


async def _fetch_all_prices(stock_codes: list[str]) -> dict[str, dict]:
    async with httpx.AsyncClient() as client:
        return {code: await fetch_stock_price(client, code) for code in stock_codes}


def _get_or_create_asset(db: Session, code: str, name_kr: str) -> DimAsset:
    asset = db.query(DimAsset).filter_by(code=code).first()
    if asset is None:
        asset_type = AssetType.ETF.value if code in _BOND_ETF_CODES else AssetType.EQUITY.value
        asset = DimAsset(asset_type=asset_type, code=code, name_kr=name_kr, currency="KRW")
        db.add(asset)
        db.commit()
        db.refresh(asset)
    return asset


def _upsert_price(db: Session, asset: DimAsset, trade_date, output: dict) -> None:
    row = db.query(FactMarketDaily).filter_by(asset_id=asset.asset_id, trade_date=trade_date).first()
    if row is None:
        # 당일 시세를 당일 조회하므로 knowledge_date = trade_date
        # (app/db/point_in_time.py 참고).
        row = FactMarketDaily(asset_id=asset.asset_id, trade_date=trade_date, knowledge_date=trade_date)
        db.add(row)
    row.open = output.get("stck_oprc")
    row.high = output.get("stck_hgpr")
    row.low = output.get("stck_lwpr")
    row.close = output.get("stck_prpr")
    row.adj_close = output.get("stck_prpr")
    row.volume = output.get("acml_vol")
    row.source = "kis"


def run() -> None:
    db = SessionLocal()
    try:
        with track_ingestion_run(db, "kis_korean_equity_prices") as ingestion:
            prices = asyncio.run(_fetch_all_prices(list(SYMBOLS.keys())))
            trade_date = datetime.now(timezone.utc).date()

            for code, output in prices.items():
                asset = _get_or_create_asset(db, code, SYMBOLS[code])
                _upsert_price(db, asset, trade_date, output)

            db.commit()
            ingestion.raw_archive_path = "data/raw_archive/kis"
    finally:
        db.close()
