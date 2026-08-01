import httpx
import pytest
import respx

from app.ingestion.connectors.yahoo_finance_client import YahooFinanceApiError, fetch_daily_history


def _chart_json(timestamps, closes, opens=None, highs=None, lows=None, volumes=None) -> dict:
    n = len(timestamps)
    return {
        "chart": {
            "result": [
                {
                    "meta": {"symbol": "XLE", "currency": "USD"},
                    "timestamp": timestamps,
                    "indicators": {
                        "quote": [
                            {
                                "open": opens or [None] * n,
                                "high": highs or [None] * n,
                                "low": lows or [None] * n,
                                "close": closes,
                                "volume": volumes or [None] * n,
                            }
                        ]
                    },
                }
            ],
            "error": None,
        }
    }


@respx.mock
async def test_fetch_daily_history_returns_rows_in_order():
    respx.get("https://query1.finance.yahoo.com/v8/finance/chart/XLE").mock(
        return_value=httpx.Response(
            200,
            json=_chart_json(
                [1000, 2000, 3000],
                [95.0, 96.5, 97.2],
                opens=[94.0, 95.5, 96.8],
                highs=[95.5, 97.0, 97.5],
                lows=[93.5, 95.0, 96.5],
                volumes=[100, 200, 300],
            ),
        )
    )
    async with httpx.AsyncClient() as client:
        rows = await fetch_daily_history(client, "XLE")

    assert len(rows) == 3
    assert rows[-1]["close"] == 97.2
    assert rows[-1]["volume"] == 300


@respx.mock
async def test_fetch_daily_history_skips_null_close_slots():
    """휴장일 등 결측 슬롯(close=None)은 건너뛴다."""
    respx.get("https://query1.finance.yahoo.com/v8/finance/chart/XLE").mock(
        return_value=httpx.Response(200, json=_chart_json([1000, 2000], [95.0, None]))
    )
    async with httpx.AsyncClient() as client:
        rows = await fetch_daily_history(client, "XLE")

    assert len(rows) == 1
    assert rows[0]["close"] == 95.0


@respx.mock
async def test_fetch_daily_history_raises_on_missing_result():
    respx.get("https://query1.finance.yahoo.com/v8/finance/chart/BADSYM").mock(
        return_value=httpx.Response(200, json={"chart": {"result": None, "error": {"code": "Not Found"}}})
    )
    async with httpx.AsyncClient() as client:
        with pytest.raises(YahooFinanceApiError):
            await fetch_daily_history(client, "BADSYM")
