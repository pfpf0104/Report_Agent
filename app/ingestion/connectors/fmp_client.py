"""Financial Modeling Prep API 클라이언트. 시세·기업 프로필용.

문서: https://site.financialmodelingprep.com/developer/docs

주: 예전 /api/v3 경로+path parameter 방식(/quote/{symbol})은 폐지됐다. 실제 키로
라이브 검증한 결과 /stable 경로+query parameter(?symbol=) 방식만 200을 반환한다
(로컬 PC에서 GET https://financialmodelingprep.com/stable/quote?symbol=AAPL&apikey=...
실측 확인, 2026-08 기준). 응답 포맷 자체(list[dict])는 그대로다.
"""
from __future__ import annotations

import httpx

from app.core.config import settings
from app.ingestion.connectors.http_utils import archive_raw_response, request_with_retry

BASE_URL = "https://financialmodelingprep.com/stable"


class FmpApiError(RuntimeError):
    pass


def _require_api_key() -> str:
    if not settings.fmp_api_key:
        raise FmpApiError("REPORT_AGENT_FMP_API_KEY가 설정되지 않았다")
    return settings.fmp_api_key


async def fetch_quote(client: httpx.AsyncClient, symbol: str) -> dict:
    api_key = _require_api_key()
    url = f"{BASE_URL}/quote"
    response = await request_with_retry(client, "GET", url, params={"symbol": symbol, "apikey": api_key})
    response.raise_for_status()
    archive_raw_response("fmp", url, response.content)

    payload = response.json()
    if not payload:
        raise FmpApiError(f"FMP에 {symbol} quote 데이터 없음")
    return payload[0]


async def fetch_profile(client: httpx.AsyncClient, symbol: str) -> dict:
    api_key = _require_api_key()
    url = f"{BASE_URL}/profile"
    response = await request_with_retry(client, "GET", url, params={"symbol": symbol, "apikey": api_key})
    response.raise_for_status()
    archive_raw_response("fmp", url, response.content)

    payload = response.json()
    if not payload:
        raise FmpApiError(f"FMP에 {symbol} profile 데이터 없음")
    return payload[0]


async def fetch_key_metrics(
    client: httpx.AsyncClient, symbol: str, *, period: str = "annual", limit: int = 5
) -> list[dict]:
    """연도별 핵심 재무지표(ROE·BPS 등)를 최신순으로 반환한다.

    FMP 공식 문서(/stable/key-metrics) 기준 응답 필드: date, fiscalYear, period,
    returnOnEquity(소수, 0.25=25%), bookValuePerShare 등. 한국 종목(DART)과 달리
    미국 상장사는 이 엔드포인트 하나로 ROE·BPS를 직접 받는다 — 자본총계를
    발행주식총수로 나누는 별도 계산이 필요 없다.
    """
    api_key = _require_api_key()
    url = f"{BASE_URL}/key-metrics"
    response = await request_with_retry(
        client, "GET", url, params={"symbol": symbol, "period": period, "limit": limit, "apikey": api_key}
    )
    response.raise_for_status()
    archive_raw_response("fmp", url, response.content)

    payload = response.json()
    if not payload:
        raise FmpApiError(f"FMP에 {symbol} key-metrics 데이터 없음")
    return payload
