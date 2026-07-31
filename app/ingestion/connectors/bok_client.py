"""한국은행 ECOS(경제통계시스템) OpenAPI 클라이언트.

문서: https://ecos.bok.or.kr/api/#/DevGuide/DevGuide
엔드포인트: /StatisticSearch/{key}/json/kr/{start_no}/{end_no}/{stat_code}/{cycle}/{start}/{end}/{item_code1}
"""
from __future__ import annotations

import httpx

from app.core.config import settings
from app.ingestion.connectors.http_utils import archive_raw_response, request_with_retry

BASE_URL = "https://ecos.bok.or.kr/api"


class BokApiError(RuntimeError):
    pass


def _require_api_key() -> str:
    if not settings.bok_api_key:
        raise BokApiError("REPORT_AGENT_BOK_API_KEY가 설정되지 않았다")
    return settings.bok_api_key


async def fetch_statistic_search(
    client: httpx.AsyncClient,
    stat_code: str,
    cycle: str,
    start: str,
    end: str,
    item_code1: str,
    *,
    start_no: int = 1,
    end_no: int = 100,
) -> list[dict]:
    """단일 통계표·단일 기간 조회."""
    api_key = _require_api_key()
    url = (
        f"{BASE_URL}/StatisticSearch/{api_key}/json/kr/{start_no}/{end_no}/"
        f"{stat_code}/{cycle}/{start}/{end}/{item_code1}"
    )
    response = await request_with_retry(client, "GET", url, extra_secrets=[api_key])
    response.raise_for_status()
    archive_raw_response("bok", url, response.content, extra_secrets=[api_key])

    payload = response.json()
    if "RESULT" in payload:
        result = payload["RESULT"]
        raise BokApiError(f"BOK ECOS API 오류: code={result.get('CODE')} message={result.get('MESSAGE')}")
    return payload.get("StatisticSearch", {}).get("row", [])
