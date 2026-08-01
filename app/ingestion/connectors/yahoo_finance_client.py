"""Yahoo Finance 비공식 차트 API 클라이언트. ETF/주식 시세용(무료, API 키 불필요).

문서화된 공식 API가 아니라 웹 프론트엔드가 쓰는 내부 엔드포인트다 — 예고 없이
스펙이 바뀌거나 막힐 수 있다는 전제로 쓴다. FMP 무료 플랜이 XLE/QQQ 같은 일부
ETF를 402(구독 필요)로 막아 대체 소스로 도입했다(2026-08 실측: XLE는 FMP에서
402, Yahoo에서는 정상 응답).

REPORT_AGENT_YAHOO_FINANCE_USER_AGENT로 User-Agent를 지정한다 — 기본 브라우저
User-Agent 없이 호출하면 차단되는 사례가 있어 명시적으로 관리한다.
"""
from __future__ import annotations

import httpx

from app.ingestion.connectors.http_utils import archive_raw_response, request_with_retry

BASE_URL = "https://query1.finance.yahoo.com/v8/finance/chart"
DEFAULT_USER_AGENT = "financial-report-pipeline/0.1 (local research)"


class YahooFinanceApiError(RuntimeError):
    pass


async def fetch_daily_history(
    client: httpx.AsyncClient, symbol: str, *, range_: str = "5d", interval: str = "1d"
) -> list[dict]:
    """일별 OHLCV 리스트를 오름차순(과거→최근)으로 반환한다.

    range_는 Yahoo가 받는 상대 구간 문자열이다("5d", "1mo", "1y", "5y" 등).
    5년 백필처럼 긴 구간이 필요하면 range_="5y"를 쓴다.
    """
    url = f"{BASE_URL}/{symbol}"
    headers = {"User-Agent": DEFAULT_USER_AGENT}
    response = await request_with_retry(
        client, "GET", url, params={"range": range_, "interval": interval}, headers=headers
    )
    response.raise_for_status()
    archive_raw_response("yahoo_finance", url, response.content)

    payload = response.json()
    result = (payload.get("chart") or {}).get("result")
    if not result:
        error = (payload.get("chart") or {}).get("error")
        raise YahooFinanceApiError(f"Yahoo Finance API 오류: {error}")

    data = result[0]
    timestamps = data.get("timestamp") or []
    quote = (data.get("indicators") or {}).get("quote", [{}])[0]

    rows = []
    for i, ts in enumerate(timestamps):
        close = quote.get("close", [None] * len(timestamps))[i]
        if close is None:  # 휴장일 등으로 값이 비어있는 슬롯은 건너뛴다
            continue
        rows.append(
            {
                "timestamp": ts,
                "open": quote.get("open", [None] * len(timestamps))[i],
                "high": quote.get("high", [None] * len(timestamps))[i],
                "low": quote.get("low", [None] * len(timestamps))[i],
                "close": close,
                "volume": quote.get("volume", [None] * len(timestamps))[i],
            }
        )
    return rows
