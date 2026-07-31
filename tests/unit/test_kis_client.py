import httpx
import pytest
import respx

import app.ingestion.connectors.kis_client as kis_client
from app.ingestion.connectors.kis_client import KisApiError, fetch_stock_price


@pytest.fixture(autouse=True)
def _set_credentials(monkeypatch):
    monkeypatch.setattr(kis_client.settings, "kis_app_key", "test-app-key")
    monkeypatch.setattr(kis_client.settings, "kis_app_secret", "test-app-secret")
    monkeypatch.setattr(kis_client.settings, "kis_base_url", "https://openapivts.koreainvestment.com:29443")
    kis_client._token_cache.clear()
    yield
    kis_client._token_cache.clear()


async def test_fetch_stock_price_without_credentials_raises(monkeypatch):
    monkeypatch.setattr(kis_client.settings, "kis_app_key", None)
    async with httpx.AsyncClient() as client:
        with pytest.raises(KisApiError):
            await fetch_stock_price(client, "005930")


@respx.mock
async def test_fetch_stock_price_success():
    respx.post("https://openapivts.koreainvestment.com:29443/oauth2/tokenP").mock(
        return_value=httpx.Response(200, json={"access_token": "tok123", "expires_in": 86400})
    )
    respx.get("https://openapivts.koreainvestment.com:29443/uapi/domestic-stock/v1/quotations/inquire-price").mock(
        return_value=httpx.Response(200, json={"rt_cd": "0", "msg1": "정상", "output": {"stck_prpr": "208500"}})
    )
    async with httpx.AsyncClient() as client:
        output = await fetch_stock_price(client, "005930")
    assert output["stck_prpr"] == "208500"


@respx.mock
async def test_fetch_stock_price_reuses_cached_token():
    token_route = respx.post("https://openapivts.koreainvestment.com:29443/oauth2/tokenP").mock(
        return_value=httpx.Response(200, json={"access_token": "tok123", "expires_in": 86400})
    )
    respx.get("https://openapivts.koreainvestment.com:29443/uapi/domestic-stock/v1/quotations/inquire-price").mock(
        return_value=httpx.Response(200, json={"rt_cd": "0", "output": {"stck_prpr": "208500"}})
    )
    async with httpx.AsyncClient() as client:
        await fetch_stock_price(client, "005930")
        await fetch_stock_price(client, "000660")
    assert token_route.call_count == 1  # 두 번째 호출은 캐시된 토큰을 재사용해야 한다


@respx.mock
async def test_fetch_stock_price_api_error_raises():
    respx.post("https://openapivts.koreainvestment.com:29443/oauth2/tokenP").mock(
        return_value=httpx.Response(200, json={"access_token": "tok123", "expires_in": 86400})
    )
    respx.get("https://openapivts.koreainvestment.com:29443/uapi/domestic-stock/v1/quotations/inquire-price").mock(
        return_value=httpx.Response(200, json={"rt_cd": "1", "msg1": "종목코드 오류"})
    )
    async with httpx.AsyncClient() as client:
        with pytest.raises(KisApiError):
            await fetch_stock_price(client, "BADCODE")


@respx.mock
async def test_fetch_stock_price_does_not_leak_secrets_into_archive():
    # tests/conftest.py의 autouse 픽스처가 RAW_ARCHIVE_DIR을 이미 tmp_path로 격리해준다.
    import app.ingestion.connectors.http_utils as http_utils

    respx.post("https://openapivts.koreainvestment.com:29443/oauth2/tokenP").mock(
        return_value=httpx.Response(200, json={"access_token": "REALTOKEN999", "expires_in": 86400})
    )
    respx.get("https://openapivts.koreainvestment.com:29443/uapi/domestic-stock/v1/quotations/inquire-price").mock(
        return_value=httpx.Response(200, json={"rt_cd": "0", "output": {"stck_prpr": "208500"}})
    )
    async with httpx.AsyncClient() as client:
        await fetch_stock_price(client, "005930")

    meta_files = list((http_utils.RAW_ARCHIVE_DIR / "kis").glob("*.meta.json"))
    assert meta_files
    for meta_file in meta_files:
        content = meta_file.read_text(encoding="utf-8")
        assert "test-app-key" not in content
        assert "test-app-secret" not in content
        assert "REALTOKEN999" not in content
