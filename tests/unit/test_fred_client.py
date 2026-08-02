import httpx
import pytest
import respx

import app.ingestion.connectors.fred_client as fred_client
from app.ingestion.connectors.fred_client import (
    FredApiError,
    fetch_series_observations,
    fetch_series_observations_with_first_publication_date,
)


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


@respx.mock
async def test_first_publication_date_picks_earliest_revision_per_observation():
    """핵심 회귀: 같은 관측월(date)에 여러 개정 구간(realtime_start)이 있으면
    가장 이른 것(최초 발표치)만 남아야 한다 — 나중 개정치를 쓰면 look-ahead
    bias가 생긴다(발표 당시엔 알 수 없었던 개정값을 그 시점 값처럼 쓰게 됨)."""
    respx.get("https://api.stlouisfed.org/fred/series/observations").mock(
        return_value=httpx.Response(
            200,
            json={
                "observations": [
                    {"realtime_start": "2026-02-18", "realtime_end": "2026-03-15", "date": "2026-01-01", "value": "102.34"},
                    {"realtime_start": "2026-03-16", "realtime_end": "2026-04-15", "date": "2026-01-01", "value": "102.40"},
                    {"realtime_start": "2026-03-16", "realtime_end": "9999-12-31", "date": "2026-02-01", "value": "101.90"},
                ]
            },
        )
    )
    async with httpx.AsyncClient() as client:
        rows = await fetch_series_observations_with_first_publication_date(client, "INDPRO")

    assert len(rows) == 2
    jan = next(r for r in rows if r["date"] == "2026-01-01")
    assert jan["realtime_start"] == "2026-02-18"
    assert jan["value"] == "102.34"  # 개정 전 최초 발표치 — 나중 개정치(102.40)가 아니다


@respx.mock
async def test_first_publication_date_sorts_by_observation_date():
    respx.get("https://api.stlouisfed.org/fred/series/observations").mock(
        return_value=httpx.Response(
            200,
            json={
                "observations": [
                    {"realtime_start": "2026-03-16", "realtime_end": "9999-12-31", "date": "2026-02-01", "value": "101.90"},
                    {"realtime_start": "2026-02-18", "realtime_end": "2026-03-15", "date": "2026-01-01", "value": "102.34"},
                ]
            },
        )
    )
    async with httpx.AsyncClient() as client:
        rows = await fetch_series_observations_with_first_publication_date(client, "INDPRO")

    assert [r["date"] for r in rows] == ["2026-01-01", "2026-02-01"]


async def test_first_publication_date_without_api_key_raises(monkeypatch):
    monkeypatch.setattr(fred_client.settings, "fred_api_key", None)
    async with httpx.AsyncClient() as client:
        with pytest.raises(FredApiError):
            await fetch_series_observations_with_first_publication_date(client, "INDPRO")
