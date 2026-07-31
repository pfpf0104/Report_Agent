import httpx
import pytest
import respx

import app.ingestion.connectors.fred_client as fred_client
from app.ingestion.connectors.fred_client import FredApiError, fetch_series_observations


@pytest.fixture(autouse=True)
def _set_api_key(monkeypatch):
    monkeypatch.setattr(fred_client.settings, "fred_api_key", "test-key")


async def test_fetch_series_observations_without_api_key_raises(monkeypatch):
    monkeypatch.setattr(fred_client.settings, "fred_api_key", None)
    async with httpx.AsyncClient() as client:
        with pytest.raises(FredApiError):
            await fetch_series_observations(client, "DGS10")


@respx.mock
async def test_fetch_series_observations_success():
    respx.get("https://api.stlouisfed.org/fred/series/observations").mock(
        return_value=httpx.Response(
            200, json={"observations": [{"date": "2026-07-30", "value": "4.21"}]}
        )
    )
    async with httpx.AsyncClient() as client:
        obs = await fetch_series_observations(client, "DGS10", limit=1)
    assert obs[0]["value"] == "4.21"


@respx.mock
async def test_fetch_series_observations_api_error_raises():
    respx.get("https://api.stlouisfed.org/fred/series/observations").mock(
        return_value=httpx.Response(200, json={"error_code": 400, "error_message": "Bad Request. Series id."})
    )
    async with httpx.AsyncClient() as client:
        with pytest.raises(FredApiError):
            await fetch_series_observations(client, "NOT_A_SERIES")
