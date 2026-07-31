"""금융감독원 전자공시(DART) OpenAPI 클라이언트.

문서: https://opendart.fss.or.kr/guide/main.do
- corpCode.xml: 전체 상장기업의 고유번호(corp_code) 목록을 ZIP으로 제공한다.
  자주 안 바뀌므로 인메모리에 캐시하고, force_refresh로만 다시 받는다.
- fnlttSinglAcntAll.json: 단일 회사의 전체 재무제표 계정을 조회한다.

TODO: extract_capital_total()은 지배기업소유주지분 "총액"만 뽑는다. BPS를
완성하려면 발행주식총수(별도 API 또는 다른 소스)로 나눠야 한다 — 이 클라이언트
범위 밖으로 남겨둔다.
"""
from __future__ import annotations

import io
import zipfile
from xml.etree import ElementTree

import httpx

from app.core.config import settings
from app.ingestion.connectors.http_utils import archive_raw_response, request_with_retry

BASE_URL = "https://opendart.fss.or.kr/api"

_CORP_CODE_CACHE: dict[str, str] | None = None


class DartApiError(RuntimeError):
    pass


def _require_api_key() -> str:
    if not settings.dart_api_key:
        raise DartApiError("REPORT_AGENT_DART_API_KEY가 설정되지 않았다")
    return settings.dart_api_key


async def fetch_corp_code_map(client: httpx.AsyncClient, *, force_refresh: bool = False) -> dict[str, str]:
    """회사명 -> corp_code(8자리) 맵."""
    global _CORP_CODE_CACHE
    if _CORP_CODE_CACHE is not None and not force_refresh:
        return _CORP_CODE_CACHE

    api_key = _require_api_key()
    url = f"{BASE_URL}/corpCode.xml"
    response = await request_with_retry(client, "GET", url, params={"crtfc_key": api_key})
    response.raise_for_status()
    archive_raw_response("dart", url, response.content, suffix=".zip")

    with zipfile.ZipFile(io.BytesIO(response.content)) as zf:
        xml_bytes = zf.read("CORPCODE.xml")

    root = ElementTree.fromstring(xml_bytes)
    mapping: dict[str, str] = {}
    for item in root.iter("list"):
        name = item.findtext("corp_name")
        code = item.findtext("corp_code")
        if name and code:
            mapping[name] = code

    _CORP_CODE_CACHE = mapping
    return mapping


async def fetch_single_company_financials(
    client: httpx.AsyncClient,
    corp_code: str,
    bsns_year: int,
    reprt_code: str = "11011",  # 사업보고서(연간)
    fs_div: str = "CFS",  # 연결재무제표
) -> list[dict]:
    """단일 회사의 전체 재무제표 계정 목록을 반환한다."""
    api_key = _require_api_key()
    url = f"{BASE_URL}/fnlttSinglAcntAll.json"
    params = {
        "crtfc_key": api_key,
        "corp_code": corp_code,
        "bsns_year": str(bsns_year),
        "reprt_code": reprt_code,
        "fs_div": fs_div,
    }
    response = await request_with_retry(client, "GET", url, params=params)
    response.raise_for_status()
    archive_raw_response("dart", url, response.content)

    payload = response.json()
    if payload.get("status") != "000":
        raise DartApiError(f"DART API 오류: status={payload.get('status')} message={payload.get('message')}")
    return payload.get("list", [])


def extract_capital_total(accounts: list[dict]) -> float | None:
    """전체 계정 목록에서 자본총계를 뽑는다(원 단위)."""
    for account in accounts:
        if account.get("account_nm") == "자본총계":
            try:
                return float(account["thstrm_amount"].replace(",", ""))
            except (KeyError, ValueError, AttributeError):
                continue
    return None
