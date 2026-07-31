import io
import zipfile

import httpx
import pytest
import respx

import app.ingestion.connectors.dart_client as dart_client
from app.ingestion.connectors.dart_client import (
    DartApiError,
    extract_capital_total,
    fetch_corp_code_map,
    fetch_single_company_financials,
)


@pytest.fixture(autouse=True)
def _reset_corp_code_cache():
    dart_client._CORP_CODE_CACHE = None
    yield
    dart_client._CORP_CODE_CACHE = None


@pytest.fixture(autouse=True)
def _set_api_key(monkeypatch):
    monkeypatch.setattr(dart_client.settings, "dart_api_key", "test-key")


def _make_corp_code_zip() -> bytes:
    xml = (
        "<result>"
        "<list><corp_code>00126380</corp_code><corp_name>삼성전자</corp_name><stock_code>005930</stock_code></list>"
        "<list><corp_code>00164779</corp_code><corp_name>SK하이닉스</corp_name><stock_code>000660</stock_code></list>"
        "</result>"
    ).encode("utf-8")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("CORPCODE.xml", xml)
    return buf.getvalue()


async def test_fetch_corp_code_map_without_api_key_raises(monkeypatch):
    monkeypatch.setattr(dart_client.settings, "dart_api_key", None)
    async with httpx.AsyncClient() as client:
        with pytest.raises(DartApiError):
            await fetch_corp_code_map(client)


@respx.mock
async def test_fetch_corp_code_map_parses_zip():
    respx.get("https://opendart.fss.or.kr/api/corpCode.xml").mock(
        return_value=httpx.Response(200, content=_make_corp_code_zip())
    )
    async with httpx.AsyncClient() as client:
        mapping = await fetch_corp_code_map(client)
    assert mapping["삼성전자"] == "00126380"
    assert mapping["SK하이닉스"] == "00164779"


@respx.mock
async def test_fetch_corp_code_map_uses_cache_on_second_call():
    route = respx.get("https://opendart.fss.or.kr/api/corpCode.xml").mock(
        return_value=httpx.Response(200, content=_make_corp_code_zip())
    )
    async with httpx.AsyncClient() as client:
        await fetch_corp_code_map(client)
        await fetch_corp_code_map(client)
    assert route.call_count == 1


@respx.mock
async def test_fetch_single_company_financials_success():
    respx.get("https://opendart.fss.or.kr/api/fnlttSinglAcntAll.json").mock(
        return_value=httpx.Response(
            200,
            json={
                "status": "000",
                "message": "정상",
                "list": [{"account_nm": "자본총계", "thstrm_amount": "402,192,000,000,000"}],
            },
        )
    )
    async with httpx.AsyncClient() as client:
        accounts = await fetch_single_company_financials(client, "00126380", 2025)
    assert extract_capital_total(accounts) == 402_192_000_000_000.0


@respx.mock
async def test_fetch_single_company_financials_api_error_raises():
    respx.get("https://opendart.fss.or.kr/api/fnlttSinglAcntAll.json").mock(
        return_value=httpx.Response(200, json={"status": "013", "message": "조회된 데이터가 없습니다."})
    )
    async with httpx.AsyncClient() as client:
        with pytest.raises(DartApiError):
            await fetch_single_company_financials(client, "00126380", 2025)


def test_extract_capital_total_returns_none_when_missing():
    assert extract_capital_total([{"account_nm": "매출액", "thstrm_amount": "1,000"}]) is None
