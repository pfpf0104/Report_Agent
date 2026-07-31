"""Financial Modeling Prep API 클라이언트. 시세·기업 프로필용.

문서: https://site.financialmodelingprep.com/developer/docs
"""
from __future__ import annotations

import httpx

from app.core.config import settings
from app.ingestion.connectors.http_utils import archive_raw_response, request_with_retry

BASE_URL = "https://financialmodelingprep.com/api/v3"


class FmpApiError(RuntimeError):
    pass


def _require_api_key() -> str:
    if not settings.fmp_api_key:
        raise FmpApiError("REPORT_AGENT_FMP_API_KEY가 설정되지 않았다")
    return settings.fmp_api_key


async def fetch_quote(client: httpx.AsyncClient, symbol: str) -> dict:
    api_key = _require_api_key()
    url = f"{BASE_URL}/quote/{symbol}"
    response = await request_with_retry(client, "GET", url, params={"apikey": api_key})
    response.raise_for_status()
    archive_raw_response("fmp", url, response.content)

    payload = response.json()
    if not payload:
        raise FmpApiError(f"FMP에 {symbol} quote 데이터 없음")
    return payload[0]


async def fetch_profile(client: httpx.AsyncClient, symbol: str) -> dict:
    api_key = _require_api_key()
    url = f"{BASE_URL}/profile/{symbol}"
    response = await request_with_retry(client, "GET", url, params={"apikey": api_key})
    response.raise_for_status()
    archive_raw_response("fmp", url, response.content)

    payload = response.json()
    if not payload:
        raise FmpApiError(f"FMP에 {symbol} profile 데이터 없음")
    return payload[0]
