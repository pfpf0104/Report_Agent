"""API Key 인증.

Cloudflare Tunnel로 로컬 PC의 FastAPI를 외부에 노출하는 구조이므로, 실제
데이터를 다루는 엔드포인트(reports, ingestion)는 반드시 이 의존성을 거치도록
한다. 헬스체크는 업타임 모니터링 편의를 위해 인증 없이 열어둔다.
"""
from __future__ import annotations

import secrets

from fastapi import HTTPException, Security
from fastapi.security import APIKeyHeader

from app.core.config import settings

_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def verify_api_key(api_key: str | None = Security(_api_key_header)) -> None:
    if api_key is None or not secrets.compare_digest(api_key, settings.api_key):
        raise HTTPException(status_code=401, detail="Invalid or missing API key")
