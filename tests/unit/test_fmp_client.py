import httpx
import pytest
import respx

import app.ingestion.connectors.fmp_client as fmp_client
from app.ingestion.connectors.fmp_client import FmpApiError, fetch_profile, fetch_quote


@pytest.fixture(autouse=True)
def _set_api_key(monkeypatch):
    monkeypatch.setattr(fmp_client.settings, "fmp_api_key", "test-key")


async def test_fetch_quote_without_api_key_raises(monkeypatch):
    monkeypatch.setattr(fmp_client.settings, "fmp_api_key", None)
    async with httpx.AsyncClient() as client:
        with pytest.raises(FmpApiError):
            await fetch_quote(client, "XLE")


@respx.mock
async def test_fetch_quote_success():
    respx.get("https://financialmodelingprep.com/stable/quote", params={"symbol": "XLE"}).mock(
        return_value=httpx.Response(200, json=[{"symbol": "XLE", "price": 95.32}])
    )
    async with httpx.AsyncClient() as client:
        quote = await fetch_quote(client, "XLE")
    assert quote["price"] == 95.32


@respx.mock
async def test_fetch_quote_empty_response_raises():
    respx.get("https://financialmodelingprep.com/stable/quote", params={"symbol": "BADSYM"}).mock(
        return_value=httpx.Response(200, json=[])
    )
    async with httpx.AsyncClient() as client:
        with pytest.raises(FmpApiError):
            await fetch_quote(client, "BADSYM")


@respx.mock
async def test_fetch_profile_success():
    respx.get("https://financialmodelingprep.com/stable/profile", params={"symbol": "AAPL"}).mock(
        return_value=httpx.Response(200, json=[{"symbol": "AAPL", "companyName": "Apple Inc."}])
    )
    async with httpx.AsyncClient() as client:
        profile = await fetch_profile(client, "AAPL")
    assert profile["companyName"] == "Apple Inc."
