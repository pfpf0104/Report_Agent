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
