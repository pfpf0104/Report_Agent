"""FRED(세인트루이스 연준) API 클라이언트. City AI의 글로벌 금리 입력, 레짐 분류기의
거시경제 지표 입력용.

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


async def fetch_series_observations_with_first_publication_date(
    client: httpx.AsyncClient,
    series_id: str,
    *,
    start: str | None = None,
    end: str | None = None,
) -> list[dict]:
    """관측치별 **최초 공표일**(realtime_start)을 함께 반환한다.

    거시경제 지표(GDP·CPI·산업생산 등)는 관측월과 실제 발표일 사이에 몇 주
    지연이 있다(2026-08 실측: INDPRO 2026-01 관측치가 2026-02-18에 처음
    공개됨 — 약 6주 지연). fetch_series_observations()의 기본 응답은
    realtime_start가 "조회 시점"일 뿐이라 이 지연을 알 수 없다.

    output_type=4로 조회하면 각 관측치의 모든 개정 구간이 별도 행으로 오는데,
    realtime_start가 가장 이른 행이 최초 공표(첫 발표치)다 — 이후 행은 개정판
    (revision)이라 knowledge_date 계산에 쓰지 않는다(발표 당시 알 수 있었던
    값이 revision 전 최초 발표치이기 때문 — point-in-time 원칙과 일치).
    realtime_start를 아주 과거로 열어야(1776-07-04, FRED 문서 권장값) 그
    시리즈의 전체 개정 이력을 다 받는다.
    """
    api_key = _require_api_key()
    url = f"{BASE_URL}/series/observations"
    params: dict[str, str | int] = {
        "series_id": series_id,
        "api_key": api_key,
        "file_type": "json",
        "realtime_start": "1776-07-04",
        "realtime_end": "9999-12-31",
        "output_type": 4,
        "limit": 100_000,
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

    all_rows = payload.get("observations", [])
    earliest_by_date: dict[str, dict] = {}
    for row in all_rows:
        obs_date = row["date"]
        existing = earliest_by_date.get(obs_date)
        if existing is None or row["realtime_start"] < existing["realtime_start"]:
            earliest_by_date[obs_date] = row
    return sorted(earliest_by_date.values(), key=lambda r: r["date"])
