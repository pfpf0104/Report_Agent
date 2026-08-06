import httpx
import pytest
import respx

import app.ingestion.connectors.sec_edgar_client as sec_edgar_client
from app.ingestion.connectors.sec_edgar_client import (
    SecEdgarApiError,
    fetch_cik_for_ticker,
    fetch_company_concept,
)

TICKER_MAP_URL = "https://www.sec.gov/files/company_tickers.json"
FACTS_URL = "https://data.sec.gov/api/xbrl/companyconcept/CIK0000723125/us-gaap/StockholdersEquity.json"


@pytest.fixture(autouse=True)
def _set_user_agent(monkeypatch):
    monkeypatch.setattr(sec_edgar_client.settings, "sec_edgar_user_agent", "Test Agent test@example.com")
    sec_edgar_client._TICKER_TO_CIK_CACHE = None
    yield
    sec_edgar_client._TICKER_TO_CIK_CACHE = None


async def test_fetch_cik_for_ticker_without_user_agent_raises(monkeypatch):
    monkeypatch.setattr(sec_edgar_client.settings, "sec_edgar_user_agent", None)
    async with httpx.AsyncClient() as client:
        with pytest.raises(SecEdgarApiError):
            await fetch_cik_for_ticker(client, "MU")


@respx.mock
async def test_fetch_cik_for_ticker_success():
    respx.get(TICKER_MAP_URL).mock(
        return_value=httpx.Response(
            200,
            json={"0": {"cik_str": 723125, "ticker": "MU", "title": "MICRON TECHNOLOGY INC"}},
        )
    )
    async with httpx.AsyncClient() as client:
        cik = await fetch_cik_for_ticker(client, "MU")
    assert cik == "0000723125"


@respx.mock
async def test_fetch_cik_for_ticker_unknown_ticker_raises():
    respx.get(TICKER_MAP_URL).mock(
        return_value=httpx.Response(200, json={"0": {"cik_str": 1, "ticker": "AAPL", "title": "Apple"}})
    )
    async with httpx.AsyncClient() as client:
        with pytest.raises(SecEdgarApiError):
            await fetch_cik_for_ticker(client, "NOTREAL")


@respx.mock
async def test_fetch_cik_for_ticker_caches_across_calls():
    route = respx.get(TICKER_MAP_URL).mock(
        return_value=httpx.Response(200, json={"0": {"cik_str": 723125, "ticker": "MU", "title": "x"}})
    )
    async with httpx.AsyncClient() as client:
        await fetch_cik_for_ticker(client, "MU")
        await fetch_cik_for_ticker(client, "MU")
    assert route.call_count == 1


@respx.mock
async def test_fetch_company_concept_success():
    respx.get(FACTS_URL).mock(
        return_value=httpx.Response(
            200,
            json={"units": {"USD": [{"end": "2026-05-28", "val": 100724000000, "fy": 2026, "fp": "Q3", "filed": "2026-06-25", "form": "10-Q"}]}},
        )
    )
    async with httpx.AsyncClient() as client:
        rows = await fetch_company_concept(client, "0000723125", "StockholdersEquity")
    assert len(rows) == 1
    assert rows[0]["val"] == 100724000000


@respx.mock
async def test_fetch_company_concept_404_returns_empty_list():
    """회사가 이 태그를 아예 공시하지 않은 경우 — 예외가 아니라 빈 리스트."""
    respx.get(FACTS_URL).mock(return_value=httpx.Response(404))
    async with httpx.AsyncClient() as client:
        rows = await fetch_company_concept(client, "0000723125", "StockholdersEquity")
    assert rows == []
