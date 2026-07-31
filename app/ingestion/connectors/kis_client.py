"""한국투자증권(KIS) OpenAPI 클라이언트. 국내 주식 현재가 조회용.

문서: https://apiportal.koreainvestment.com
모의투자(REPORT_AGENT_KIS_USE_MOCK=true, 기본값)와 실전투자는 base_url이
다르다(app/core/config.py 참고). OAuth 토큰은 발급 후 캐싱해 재사용한다
(KIS는 토큰 발급 자체에도 호출 빈도 제한이 있다).

TODO(확인 필요): tr_id="FHKST01010100"과 output 필드명(stck_prpr 등)은
KIS 개발자센터 문서 기준이다 — 이 세션은 네트워크가 막혀 있어 실제 응답으로
검증하지 못했다.
"""
from __future__ import annotations

import time

import httpx

from app.core.config import settings
from app.ingestion.connectors.http_utils import archive_raw_response, request_with_retry

_token_cache: dict[str, float | str] = {}


class KisApiError(RuntimeError):
    pass


def _require_credentials() -> tuple[str, str]:
    if not settings.kis_app_key or not settings.kis_app_secret:
        raise KisApiError("REPORT_AGENT_KIS_APP_KEY/SECRET이 설정되지 않았다")
    return settings.kis_app_key, settings.kis_app_secret


async def _get_access_token(client: httpx.AsyncClient) -> str:
    """OAuth 토큰을 발급받아 캐싱한다. 만료 60초 전까지는 캐시를 재사용한다.

    토큰 발급은 POST라 request_with_retry(GET 전용)를 쓰지 않는다 — 재시도
    대상에서 의도적으로 제외한다(중복 발급으로 인한 호출 한도 소모 방지).
    """
    now = time.time()
    cached_token = _token_cache.get("token")
    cached_expiry = _token_cache.get("expires_at", 0)
    if cached_token and isinstance(cached_expiry, (int, float)) and cached_expiry > now + 60:
        return str(cached_token)

    app_key, app_secret = _require_credentials()
    url = f"{settings.kis_base_url}/oauth2/tokenP"
    response = await client.post(
        url,
        json={"grant_type": "client_credentials", "appkey": app_key, "appsecret": app_secret},
        timeout=10,
    )
    response.raise_for_status()
    payload = response.json()

    token = payload.get("access_token")
    if not token:
        raise KisApiError(f"KIS 토큰 발급 실패: {payload}")

    expires_in = int(payload.get("expires_in", 86400))
    _token_cache["token"] = token
    _token_cache["expires_at"] = now + expires_in
    return token


async def fetch_stock_price(client: httpx.AsyncClient, stock_code: str) -> dict:
    """국내주식 현재가 시세 조회. stock_code는 6자리 종목코드(예: 005930)."""
    app_key, app_secret = _require_credentials()
    token = await _get_access_token(client)

    url = f"{settings.kis_base_url}/uapi/domestic-stock/v1/quotations/inquire-price"
    headers = {
        "authorization": f"Bearer {token}",
        "appkey": app_key,
        "appsecret": app_secret,
        "tr_id": "FHKST01010100",
    }
    params = {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": stock_code}
    secrets = [app_key, app_secret, token]

    response = await request_with_retry(client, "GET", url, params=params, headers=headers, extra_secrets=secrets)
    response.raise_for_status()
    archive_raw_response("kis", url, response.content, extra_secrets=secrets)

    payload = response.json()
    if payload.get("rt_cd") != "0":
        raise KisApiError(f"KIS API 오류: rt_cd={payload.get('rt_cd')} msg={payload.get('msg1')}")
    return payload.get("output", {})
