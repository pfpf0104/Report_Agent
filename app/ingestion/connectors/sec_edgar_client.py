"""SEC EDGAR XBRL company facts API 클라이언트. 미국 상장사 재무제표용(무료,
API 키 불필요) — Micron 등 FMP 무료 플랜에서 막힌 종목의 대안(2026-08 실측:
FMP `/stable/key-metrics`가 MU에서만 402를 반환, INTC/AMD/TSLA/MSFT는 정상
— 심볼별 프리미엄 제한으로 확인됨).

문서: https://www.sec.gov/edgar/sec-api-documentation

SEC 정책상 User-Agent에 식별 가능한 이메일이 없으면 모든 요청이 403으로
거부된다(실측 확인) — REPORT_AGENT_SEC_EDGAR_USER_AGENT로 관리한다.
"""
from __future__ import annotations

import httpx

from app.core.config import settings
from app.ingestion.connectors.http_utils import archive_raw_response, request_with_retry

TICKER_MAP_URL = "https://www.sec.gov/files/company_tickers.json"
FACTS_BASE_URL = "https://data.sec.gov/api/xbrl/companyconcept"

_TICKER_TO_CIK_CACHE: dict[str, str] | None = None


class SecEdgarApiError(RuntimeError):
    pass


def _require_user_agent() -> str:
    if not settings.sec_edgar_user_agent:
        raise SecEdgarApiError("REPORT_AGENT_SEC_EDGAR_USER_AGENT가 설정되지 않았다")
    return settings.sec_edgar_user_agent


async def fetch_cik_for_ticker(client: httpx.AsyncClient, ticker: str, *, force_refresh: bool = False) -> str:
    """티커(예: "MU") -> 10자리 0패딩 CIK 문자열(예: "0000723125")."""
    global _TICKER_TO_CIK_CACHE
    user_agent = _require_user_agent()

    if _TICKER_TO_CIK_CACHE is None or force_refresh:
        response = await request_with_retry(
            client, "GET", TICKER_MAP_URL, headers={"User-Agent": user_agent}
        )
        response.raise_for_status()
        archive_raw_response("sec_edgar", TICKER_MAP_URL, response.content)

        payload = response.json()
        _TICKER_TO_CIK_CACHE = {
            row["ticker"]: str(row["cik_str"]).zfill(10) for row in payload.values()
        }

    cik = _TICKER_TO_CIK_CACHE.get(ticker.upper())
    if cik is None:
        raise SecEdgarApiError(f"SEC EDGAR 티커 맵에 {ticker} 없음")
    return cik


async def fetch_company_concept(client: httpx.AsyncClient, cik: str, tag: str) -> list[dict]:
    """단일 XBRL 태그(예: "StockholdersEquity")의 전체 이력을 반환한다.

    반환 형식은 SEC 응답의 units.USD(또는 units.shares) 리스트 그대로다 —
    각 원소는 {start, end, val, fy, fp, form, filed, accn, ...}. cik는
    fetch_cik_for_ticker가 반환하는 10자리 0패딩 문자열이어야 한다.
    """
    user_agent = _require_user_agent()
    url = f"{FACTS_BASE_URL}/CIK{cik}/us-gaap/{tag}.json"
    response = await request_with_retry(client, "GET", url, headers={"User-Agent": user_agent})
    if response.status_code == 404:
        return []  # 이 회사가 이 태그를 아예 공시하지 않은 경우(정상 상황)
    response.raise_for_status()
    archive_raw_response("sec_edgar", url, response.content)

    payload = response.json()
    units = payload.get("units", {})
    return units.get("USD") or units.get("shares") or []
