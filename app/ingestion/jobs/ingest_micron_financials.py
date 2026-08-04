"""FMP에서 마이크론(MU) 분기 재무지표(ROE·BPS)를 가져와 fact_financial_quarterly에
적재하는 배치.

밸류에이션 리포트의 "산업·경쟁 분석"(MASTER_PLAN Phase 4-4)이 삼성전자·SK하이닉스
대비 경쟁사 실적을 실측으로 비교하려면 마이크론 재무 데이터가 있어야 한다 —
이 배치가 그 데이터 소스다. 지금까지 competitor(마이크론) 데이터는 이 프로젝트에
전혀 없었다(정성적 서술만 가능했음).

DART 배치(ingest_financial_statements.py)와 다른 점: DART는 자본총계를 조회해
발행주식총수로 나눠 BPS를 직접 계산해야 하지만, FMP의 key-metrics 엔드포인트는
ROE·BPS를 이미 계산된 값으로 반환한다 — 별도 계산이 필요 없다.

knowledge_date: FMP key-metrics 응답의 `date`(회계분기 마감일) 필드를 그대로
쓰지 않는다 — 그건 "그 분기가 언제 끝났는가"이지 "그 분기 실적이 언제 공개됐는가"가
아니다(look-ahead bias 방지 원칙, app/db/point_in_time.py 참고). 미국 상장사는
분기보고서(10-Q) 법정 제출기한이 마감 후 40일(대형 신고인 기준)이라, 마감일에
그 기한을 더한 근사치를 knowledge_date로 쓴다 — DART처럼 실제 접수번호로 정확한
공시일을 복원할 필드가 이 엔드포인트 응답에는 없어 근사를 명시적으로 표시한다.
"""
from __future__ import annotations

import asyncio
from datetime import date, timedelta

import httpx
from sqlalchemy.orm import Session

from app.db.base import SessionLocal
from app.db.models.dim_asset import AssetType, DimAsset
from app.db.models.fact_financial_quarterly import FactFinancialQuarterly
from app.ingestion.connectors.fmp_client import fetch_key_metrics
from app.ingestion.run_tracker import track_ingestion_run

MICRON_SYMBOL = "MU"

# 10-Q 법정 제출기한 근사(대형 신고인 40일) — 실제 공시일은 이보다 이를 수 있지만
# 이보다 늦을 수는 없으므로, look-ahead를 만들지 않는 보수적 근사다.
FILING_LAG_DAYS = 40


def _get_or_create_asset(db: Session) -> DimAsset:
    asset = db.query(DimAsset).filter_by(code=MICRON_SYMBOL).first()
    if asset is None:
        asset = DimAsset(
            asset_type=AssetType.EQUITY.value, code=MICRON_SYMBOL, name_kr="마이크론(Micron)", currency="USD"
        )
        db.add(asset)
        db.commit()
        db.refresh(asset)
    return asset


async def _fetch_quarterly_metrics(limit: int) -> list[dict]:
    async with httpx.AsyncClient() as client:
        return await fetch_key_metrics(client, MICRON_SYMBOL, period="quarter", limit=limit)


def run(limit: int = 8) -> None:
    """최근 limit개 분기(기본 8개, 2년치) 재무지표를 적재한다."""
    db = SessionLocal()
    try:
        with track_ingestion_run(db, "fmp_micron_financials") as ingestion:
            # FmpApiError는 여기서 잡지 않는다 — track_ingestion_run이 예외를
            # status="failed"로 기록하므로, 부분 실패를 성공으로 위장하지 않으려면
            # 그대로 전파돼야 한다.
            rows = asyncio.run(_fetch_quarterly_metrics(limit))

            asset = _get_or_create_asset(db)
            for metrics in rows:
                fiscal_year = metrics.get("fiscalYear")
                period = metrics.get("period")  # "Q1".."Q4"
                roe = metrics.get("returnOnEquity")
                bps = metrics.get("bookValuePerShare")
                period_end = metrics.get("date")
                if fiscal_year is None or period is None or period_end is None:
                    continue
                if not period.upper().startswith("Q"):
                    continue
                try:
                    fiscal_quarter = int(period[1])
                except (ValueError, IndexError):
                    continue

                knowledge_date = date.fromisoformat(period_end) + timedelta(days=FILING_LAG_DAYS)

                row = (
                    db.query(FactFinancialQuarterly)
                    .filter_by(asset_id=asset.asset_id, fiscal_year=fiscal_year, fiscal_quarter=fiscal_quarter)
                    .first()
                )
                if row is None:
                    row = FactFinancialQuarterly(
                        asset_id=asset.asset_id,
                        fiscal_year=fiscal_year,
                        fiscal_quarter=fiscal_quarter,
                        knowledge_date=knowledge_date,
                    )
                    db.add(row)
                if bps is not None:
                    row.bps = bps
                if roe is not None:
                    row.roe = roe  # FMP는 소수(0.25=25%)로 반환 — DB 컬럼도 소수로 통일
                row.source = "fmp"

            db.commit()
            ingestion.raw_archive_path = "data/raw_archive/fmp"
    finally:
        db.close()
