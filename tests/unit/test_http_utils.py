import httpx
import pytest
import respx

from app.ingestion.connectors.http_utils import mask_sensitive_query_params, request_with_retry


def test_mask_sensitive_query_params_masks_known_keys():
    url = "https://example.com/api?crtfc_key=SECRET123&corp_code=00126380"
    masked = mask_sensitive_query_params(url)
    assert "SECRET123" not in masked
    assert "crtfc_key=%2A%2A%2A" in masked or "crtfc_key=***" in masked
    assert "corp_code=00126380" in masked


def test_mask_sensitive_query_params_noop_without_query():
    url = "https://example.com/api"
    assert mask_sensitive_query_params(url) == url


def test_mask_sensitive_query_params_masks_path_embedded_secret():
    # BOK ECOS처럼 키가 쿼리스트링이 아니라 경로에 직접 박혀 있는 경우.
    # extra_secrets 없이는 마스킹되지 않는다는 걸 먼저 확인(회귀 방지용 대조군).
    url = "https://ecos.bok.or.kr/api/StatisticSearch/MY_SECRET/json/kr/1/100/722Y001"
    assert "MY_SECRET" in mask_sensitive_query_params(url)

    masked = mask_sensitive_query_params(url, extra_secrets=["MY_SECRET"])
    assert "MY_SECRET" not in masked
    assert "/StatisticSearch/***/json/kr/1/100/722Y001" in masked


@respx.mock
async def test_request_with_retry_returns_4xx_without_retrying():
    route = respx.get("https://example.com/bad").mock(return_value=httpx.Response(401))
    async with httpx.AsyncClient() as client:
        response = await request_with_retry(client, "GET", "https://example.com/bad", max_retries=3, base_delay=0)
    assert response.status_code == 401
    assert route.call_count == 1


@respx.mock
async def test_request_with_retry_succeeds_first_try():
    route = respx.get("https://example.com/ok").mock(return_value=httpx.Response(200, json={"ok": True}))
    async with httpx.AsyncClient() as client:
        response = await request_with_retry(client, "GET", "https://example.com/ok", max_retries=3, base_delay=0)
    assert response.status_code == 200
    assert route.call_count == 1


@respx.mock
async def test_request_with_retry_recovers_after_transient_500():
    route = respx.get("https://example.com/flaky").mock(
        side_effect=[httpx.Response(500), httpx.Response(500), httpx.Response(200, json={"ok": True})]
    )
    async with httpx.AsyncClient() as client:
        response = await request_with_retry(
            client, "GET", "https://example.com/flaky", max_retries=3, base_delay=0
        )
    assert response.status_code == 200
    assert route.call_count == 3


@respx.mock
async def test_request_with_retry_exhausts_and_raises():
    route = respx.get("https://example.com/down").mock(return_value=httpx.Response(500))
    async with httpx.AsyncClient() as client:
        with pytest.raises(httpx.HTTPStatusError):
            await request_with_retry(client, "GET", "https://example.com/down", max_retries=2, base_delay=0)
    assert route.call_count == 2


async def test_request_with_retry_rejects_non_get():
    async with httpx.AsyncClient() as client:
        with pytest.raises(ValueError):
            await request_with_retry(client, "POST", "https://example.com/x")
