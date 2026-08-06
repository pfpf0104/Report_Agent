"""SEC EDGAR에서 마이크론(MU) 분기 재무지표(자기자본·순이익·발행주식수)를
가져와 BPS·ROE를 계산해 fact_financial_quarterly에 적재하는 배치.

## FMP에서 SEC EDGAR로 바꾼 이유

원래 FMP `/stable/key-metrics`로 ROE·BPS를 직접 받으려 했으나, 2026-08 라이브
검증 결과 이 프로젝트의 FMP 플랜에서 MU만 402(구독 필요)를 반환했다 —
INTC/AMD/TSLA/MSFT 등 다른 대형주는 정상 응답해, 플랜 자체가 아니라 심볼별
프리미엄 제한임을 확인했다. AAPL 응답에서도 `bookValuePerShare` 필드가
`None`으로 비어 있어(2026-08 실측), 설령 접근이 됐어도 이 필드에 의존할 수는
없었다. SEC EDGAR company facts API(완전 무료, API 키 불필요, 공식 정부
소스)로 전환해 DART와 같은 방식(자기자본÷발행주식수로 BPS 직접 계산, 순이익
÷자기자본으로 ROE 계산)으로 재구성했다.

## 단일 분기 값 골라내기

SEC의 NetIncomeLoss 같은 구간(duration) 태그는 같은 회계분기에 대해 "이번
분기만"과 "연초 누적"이 섞여 응답에 함께 들어온다(예: FY2026 Q2 응답에
start=2025-11-28~end=2026-02-26인 단일분기 행과 start=2025-08-29~end=2026-02-26
인 반기누적 행이 둘 다 있음, 2026-08 실측). start~end 구간 길이가 대략
1분기(70~100일)인 행만 골라야 단일분기 값이다. StockholdersEquity 같은
시점(instant) 태그는 start가 없어 이 필터가 필요 없다.

## Point-in-time

knowledge_date는 SEC 응답의 `filed`(실제 공시일) 필드를 그대로 쓴다 —
DART의 rcept_no 파싱과 같은 원칙으로, 이 필드가 이미 정확한 공시일이라 별도
근사가 필요 없다.
"""
from __future__ import annotations

import asyncio
from datetime import date, timedelta

import httpx
from sqlalchemy.orm import Session

from app.db.base import SessionLocal
from app.db.models.dim_asset import AssetType, DimAsset
from app.db.models.fact_financial_quarterly import FactFinancialQuarterly
from app.ingestion.connectors.sec_edgar_client import fetch_cik_for_ticker, fetch_company_concept
from app.ingestion.run_tracker import track_ingestion_run

MICRON_SYMBOL = "MU"

# 구간(duration) 태그에서 "단일 분기" 행만 골라내는 허용 오차 — 실제 분기는
# 89~92일이 흔하지만 회계연도 마감 등으로 며칠 차이가 날 수 있어 여유를 둔다.
_QUARTER_MIN_DAYS = 70
_QUARTER_MAX_DAYS = 100


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


def _single_quarter_rows(rows: list[dict]) -> list[dict]:
    """10-Q/10-K로 공시된 행 중, start~end 구간이 1분기 길이인 것만 남긴다."""
    result = []
    for row in rows:
        if row.get("form") not in ("10-Q", "10-K"):
            continue
        start, end = row.get("start"), row.get("end")
        if not start or not end:
            continue
        days = (date.fromisoformat(end) - date.fromisoformat(start)).days
        if _QUARTER_MIN_DAYS <= days <= _QUARTER_MAX_DAYS:
            result.append(row)
    return result


def _instant_rows(rows: list[dict]) -> list[dict]:
    """시점(instant) 태그 — start가 없고 end만 있는 10-Q/10-K 행."""
    return [r for r in rows if r.get("form") in ("10-Q", "10-K") and not r.get("start") and r.get("end")]


def _dedupe_by_fiscal_period(rows: list[dict]) -> list[dict]:
    """SEC는 같은 (fy, fp)에 여러 행을 함께 보고한다(2026-08 실측: MU
    StockholdersEquity에서 확인) — 어떤 보고서든 "당기"뿐 아니라 비교 목적의
    "전년동기" 수치까지 같은 (fy, fp) 라벨로 실리는 경우가 있어(예: fy=2026
    fp=Q3 응답에 end=2025-08-28인 행과 end=2026-05-28인 행이 둘 다 있음),
    filed(공시일)만으로는 "당기" 행을 가려낼 수 없다 — 같은 filed에 여러 end가
    섞여 있기 때문이다.

    그대로 순회하면 같은 (fiscal_year, fiscal_quarter)에 두 번 INSERT를 시도해
    UniqueViolation이 난다. 진짜 "당기" 값은 그 (fy, fp) 라벨을 가진 행들 중
    end가 가장 늦은 것이다 — 분기 진행 방향과 일치하므로 항상 최신 end가 해당
    분기의 실제 마감일이다."""
    latest_by_period: dict[tuple, dict] = {}
    for row in rows:
        fy, fp, filed, end = row.get("fy"), row.get("fp"), row.get("filed"), row.get("end")
        if fy is None or fp is None or filed is None or end is None:
            continue
        key = (fy, fp)
        existing = latest_by_period.get(key)
        if existing is None or end > existing["end"]:
            latest_by_period[key] = row
    return list(latest_by_period.values())


async def _fetch_all_concepts(cik: str) -> dict[str, list[dict]]:
    async with httpx.AsyncClient() as client:
        equity = await fetch_company_concept(client, cik, "StockholdersEquity")
        net_income = await fetch_company_concept(client, cik, "NetIncomeLoss")
        shares = await fetch_company_concept(client, cik, "CommonStockSharesOutstanding")
        return {"equity": equity, "net_income": net_income, "shares": shares}


def _latest_shares_before(shares_rows: list[dict], as_of_end: date) -> float | None:
    """as_of_end 시점에 가장 가까운(그 이전) 발행주식수를 찾는다."""
    candidates = [r for r in shares_rows if date.fromisoformat(r["end"]) <= as_of_end]
    if not candidates:
        return None
    latest = max(candidates, key=lambda r: r["end"])
    return float(latest["val"])


def run(cik: str | None = None) -> None:
    """cik 생략 시 SEC 티커 맵에서 MU를 조회한다(테스트에서 격리된 CIK 주입 가능)."""
    db = SessionLocal()
    try:
        with track_ingestion_run(db, "sec_edgar_micron_financials") as ingestion:
            resolved_cik = cik if cik is not None else asyncio.run(_resolve_cik())
            concepts = asyncio.run(_fetch_all_concepts(resolved_cik))

            equity_rows = _instant_rows(concepts["equity"])
            income_rows = _dedupe_by_fiscal_period(_single_quarter_rows(concepts["net_income"]))
            shares_rows = _instant_rows(concepts["shares"])

            asset = _get_or_create_asset(db)
            upserted = 0
            for eq_row in _dedupe_by_fiscal_period(equity_rows):
                end = date.fromisoformat(eq_row["end"])
                fiscal_year = eq_row.get("fy")
                fiscal_quarter = _fp_to_quarter(eq_row.get("fp"))
                filed = eq_row.get("filed")
                if fiscal_year is None or fiscal_quarter is None or filed is None:
                    continue

                shares = _latest_shares_before(shares_rows, end)
                if shares is None or shares == 0:
                    continue
                bps = float(eq_row["val"]) / shares

                # 같은 (fy, fp)의 단일분기 순이익 — ROE = 분기순이익/분기말 자기자본
                # (연환산하지 않은 단순 근사 — DART 쪽도 동일하게 연환산하지 않는다).
                income_row = next(
                    (r for r in income_rows if r.get("fy") == fiscal_year and r.get("fp") == eq_row.get("fp")),
                    None,
                )
                roe = float(income_row["val"]) / float(eq_row["val"]) if income_row else None

                knowledge_date = date.fromisoformat(filed)

                row = (
                    db.query(FactFinancialQuarterly)
                    .filter_by(asset_id=asset.asset_id, fiscal_year=fiscal_year, fiscal_quarter=fiscal_quarter)
                    .first()
                )
                if row is None:
                    row = FactFinancialQuarterly(
                        asset_id=asset.asset_id, fiscal_year=fiscal_year, fiscal_quarter=fiscal_quarter,
                        knowledge_date=knowledge_date,
                    )
                    db.add(row)
                row.bps = bps
                if roe is not None:
                    row.roe = roe
                row.knowledge_date = knowledge_date
                row.source = "sec_edgar"
                upserted += 1

            db.commit()
            ingestion.raw_archive_path = f"data/raw_archive/sec_edgar (upserted={upserted} rows)"
    finally:
        db.close()


async def _resolve_cik() -> str:
    async with httpx.AsyncClient() as client:
        return await fetch_cik_for_ticker(client, MICRON_SYMBOL)


def _fp_to_quarter(fp: str | None) -> int | None:
    """SEC의 fp("Q1".."Q4", "FY") -> fiscal_quarter(1~4). FY(연간 단일 보고)는
    이 배치가 다루는 분기 데이터가 아니므로 제외한다."""
    if fp is None or not fp.startswith("Q"):
        return None
    try:
        return int(fp[1])
    except (ValueError, IndexError):
        return None
