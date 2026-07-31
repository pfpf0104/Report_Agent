"""인제스천 커넥터 공용 HTTP 유틸: 재시도/백오프, User-Agent, raw 응답 아카이브.

민감정보(API 키가 담긴 쿼리스트링)는 아카이브에 절대 그대로 남기지 않는다 —
저장 전에 반드시 마스킹한다.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx

logger = logging.getLogger("app.ingestion")

USER_AGENT = "ReportAgent/0.1 (+https://github.com/pfpf0104/Report_Agent; ingestion-client)"
RAW_ARCHIVE_DIR = Path(__file__).resolve().parents[3] / "data" / "raw_archive"

_SENSITIVE_PARAM_NAMES = {"crtfc_key", "apikey", "api_key", "authkey", "appkey", "appsecret"}


def mask_sensitive_query_params(url: str, extra_secrets: list[str] | None = None) -> str:
    """쿼리스트링의 알려진 키 파라미터와, 명시적으로 전달된 비밀 문자열을 마스킹한다.

    extra_secrets는 BOK ECOS처럼 키를 쿼리스트링이 아니라 URL 경로에 그대로
    박아 넣는 API를 위한 것이다(예: /StatisticSearch/{key}/json/...) — 그런
    경우 쿼리스트링 파싱만으로는 절대 마스킹되지 않으므로, 호출부가 실제 키
    값을 명시적으로 넘겨야 한다.
    """
    parts = urlsplit(url)
    if parts.query:
        params = parse_qsl(parts.query, keep_blank_values=True)
        query = urlencode([(k, "***" if k.lower() in _SENSITIVE_PARAM_NAMES else v) for k, v in params])
    else:
        query = parts.query

    path = parts.path
    for secret in extra_secrets or ():
        if secret:
            path = path.replace(secret, "***")

    return urlunsplit((parts.scheme, parts.netloc, path, query, parts.fragment))


async def request_with_retry(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    params: dict | None = None,
    headers: dict | None = None,
    max_retries: int = 3,
    base_delay: float = 1.0,
    timeout: float = 10.0,
    extra_secrets: list[str] | None = None,
) -> httpx.Response:
    """GET 전용 재시도 헬퍼.

    4xx는 요청 자체가 잘못된 것이므로 재시도하지 않고 바로 반환한다(호출부가
    response.raise_for_status()로 처리). 5xx·타임아웃·연결 오류만 지수 백오프로
    재시도한다. headers는 KIS처럼 Authorization/appkey 등 커스텀 헤더가
    필요한 API를 위한 것이며, 기본 User-Agent와 병합된다(호출부 값이 우선).
    """
    if method.upper() != "GET":
        raise ValueError("GET 요청만 허용한다(데이터 변경 API 호출 금지)")

    merged_headers = {"User-Agent": USER_AGENT, **(headers or {})}

    last_exc: Exception | None = None
    for attempt in range(1, max_retries + 1):
        try:
            response = await client.get(url, params=params, timeout=timeout, headers=merged_headers)
            if response.status_code >= 500:
                raise httpx.HTTPStatusError(
                    f"서버 오류 {response.status_code}", request=response.request, response=response
                )
            return response
        except (httpx.TimeoutException, httpx.TransportError, httpx.HTTPStatusError) as exc:
            last_exc = exc
            if attempt == max_retries:
                break
            delay = base_delay * (2 ** (attempt - 1))
            logger.warning(
                "요청 재시도 %d/%d (%.1fs 대기): %s — %s",
                attempt,
                max_retries,
                delay,
                type(exc).__name__,
                mask_sensitive_query_params(url, extra_secrets),
            )
            await asyncio.sleep(delay)

    assert last_exc is not None
    raise last_exc


def archive_raw_response(
    source: str, url: str, content: bytes, suffix: str = ".json", extra_secrets: list[str] | None = None
) -> str:
    """원본 응답을 로컬에 저장한다. URL은 마스킹 후 별도 메타 파일에 기록한다.

    extra_secrets: URL 경로에 키를 직접 박아 넣는 API(BOK ECOS 등)를 위해
    실제 키 값을 넘기면 경로에서도 마스킹한다.
    """
    source_dir = RAW_ARCHIVE_DIR / source
    source_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
    data_path = source_dir / f"{timestamp}{suffix}"
    data_path.write_bytes(content)

    meta_path = source_dir / f"{timestamp}.meta.json"
    meta_path.write_text(
        json.dumps(
            {"url": mask_sensitive_query_params(url, extra_secrets), "fetched_at": timestamp},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return str(data_path.relative_to(RAW_ARCHIVE_DIR.parent.parent))
