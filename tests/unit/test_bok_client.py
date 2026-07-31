import json
from pathlib import Path

import httpx
import pytest
import respx

import app.ingestion.connectors.bok_client as bok_client
import app.ingestion.connectors.http_utils as http_utils
from app.ingestion.connectors.bok_client import BokApiError, fetch_statistic_search


@pytest.fixture(autouse=True)
def _set_api_key(monkeypatch):
    monkeypatch.setattr(bok_client.settings, "bok_api_key", "test-key")


async def test_fetch_statistic_search_without_api_key_raises(monkeypatch):
    monkeypatch.setattr(bok_client.settings, "bok_api_key", None)
    async with httpx.AsyncClient() as client:
        with pytest.raises(BokApiError):
            await fetch_statistic_search(client, "722Y001", "D", "20260101", "20260131", "0101000")


@respx.mock
async def test_fetch_statistic_search_success():
    respx.get(url__regex=r"https://ecos\.bok\.or\.kr/api/StatisticSearch/.*").mock(
        return_value=httpx.Response(
            200,
            json={
                "StatisticSearch": {
                    "row": [{"TIME": "20260130", "DATA_VALUE": "3.05", "ITEM_NAME1": "국고채(3년)"}]
                }
            },
        )
    )
    async with httpx.AsyncClient() as client:
        rows = await fetch_statistic_search(client, "722Y001", "D", "20260101", "20260131", "0101000")
    assert rows[0]["DATA_VALUE"] == "3.05"


@respx.mock
async def test_fetch_statistic_search_api_error_raises():
    respx.get(url__regex=r"https://ecos\.bok\.or\.kr/api/StatisticSearch/.*").mock(
        return_value=httpx.Response(200, json={"RESULT": {"CODE": "INFO-200", "MESSAGE": "조회된 데이터가 없습니다"}})
    )
    async with httpx.AsyncClient() as client:
        with pytest.raises(BokApiError):
            await fetch_statistic_search(client, "722Y001", "D", "20260101", "20260131", "0101000")


@respx.mock
async def test_fetch_statistic_search_does_not_leak_key_into_archive(monkeypatch):
    """회귀 테스트: BOK는 키를 쿼리스트링이 아니라 URL 경로에 넣으므로, 별도로
    extra_secrets를 넘기지 않으면 아카이브 메타 파일에 키가 그대로 남는 버그가
    있었다(발견 후 수정)."""
    monkeypatch.setattr(bok_client.settings, "bok_api_key", "REALSECRETKEY999")
    respx.get(url__regex=r"https://ecos\.bok\.or\.kr/api/StatisticSearch/.*").mock(
        return_value=httpx.Response(200, json={"StatisticSearch": {"row": [{"TIME": "20260130", "DATA_VALUE": "3.05"}]}})
    )
    async with httpx.AsyncClient() as client:
        await fetch_statistic_search(client, "722Y001", "D", "20260101", "20260131", "0101000")

    meta_files = list(Path(http_utils.RAW_ARCHIVE_DIR).glob("bok/*.meta.json"))
    assert meta_files, "아카이브 메타 파일이 생성되지 않았다"
    for meta_file in meta_files:
        content = meta_file.read_text(encoding="utf-8")
        assert "REALSECRETKEY999" not in content
        assert "***" in json.loads(content)["url"]
