"""FRED(세인트루이스 연준) API 클라이언트. City AI의 글로벌 금리 입력용.

문서: https://fred.stlouisfed.org/docs/api/fred/series_observations.html
"""
from __future__ import annotations

import httpx

from app.core.config import settings
from app.ingestion.connectors.http_utils import archive_raw_response, request_with_retry

BASE_URL = "https://api.stlouisfed.org/fred"


class FredApiError(RuntimeError):
    pass


def _require_api_key() -> str:
    if not settings.fred_api_key:
        raise FredApiError("REPORT_AGENT_FRED_API_KEY가 설정되지 않았다")
    return settings.fred_api_key


async def fetch_series_observations(
    client: httpx.AsyncClient,
    series_id: str,
    *,
    start: str | None = None,
    end: str | None = None,
    limit: int = 10,
) -> list[dict]:
    api_key = _require_api_key()
    url = f"{BASE_URL}/series/observations"
    params: dict[str, str | int] = {
        "series_id": series_id,
        "api_key": api_key,
        "file_type": "json",
        "limit": limit,
        "sort_order": "desc",
    }
    if start:
        params["observation_start"] = start
    if end:
        params["observation_end"] = end

    response = await request_with_retry(client, "GET", url, params=params)
    response.raise_for_status()
    archive_raw_response("fred", url, response.content)

    payload = response.json()
    if "error_message" in payload:
        raise FredApiError(f"FRED API 오류: {payload['error_message']}")
    return payload.get("observations", [])
