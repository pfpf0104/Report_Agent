"""로컬 계산 결과를 Supabase 서빙 캐시로 동기화한다.

무거운 인제스천·컴퓨테이션은 전부 로컬 PostgreSQL에서 하고(비용 없음),
Supabase에는 "자주 조회되는 최신 결과"만 PostgREST upsert로 올린다.
SUPABASE_URL/SUPABASE_SERVICE_KEY가 설정되지 않으면 조용히 스킵한다 —
로컬 전용으로만 써도 앱이 정상 동작해야 하기 때문이다.

동기화 실패는 리포트 생성 자체를 막지 않는다(호출부에서 백그라운드로 실행).
"""
from __future__ import annotations

import logging
from datetime import date

import httpx

from app.core.config import settings

logger = logging.getLogger("app.sync")


def _headers() -> dict | None:
    if not settings.supabase_url or not settings.supabase_service_key:
        return None
    return {
        "apikey": settings.supabase_service_key,
        "Authorization": f"Bearer {settings.supabase_service_key}",
        "Content-Type": "application/json",
        # 페이로드의 기본키(asset_id / report_type+report_date)를 충돌 대상으로
        # 삼아 upsert한다 — PostgREST는 on_conflict를 안 주면 PK를 기본으로 쓴다.
        "Prefer": "resolution=merge-duplicates,return=minimal",
    }


def sync_asset_snapshot(
    *,
    asset_id: int,
    asset_type: str,
    code: str,
    name_kr: str,
    name_en: str | None,
    sector: str | None,
    currency: str,
) -> None:
    headers = _headers()
    if headers is None:
        return

    payload = {
        "asset_id": asset_id,
        "asset_type": asset_type,
        "code": code,
        "name_kr": name_kr,
        "name_en": name_en,
        "sector": sector,
        "currency": currency,
    }
    try:
        response = httpx.post(
            f"{settings.supabase_url}/rest/v1/asset_snapshot",
            headers=headers,
            json=payload,
            timeout=10,
        )
        response.raise_for_status()
    except httpx.HTTPError as exc:
        logger.warning("asset_snapshot 동기화 실패 (asset_id=%s): %s", asset_id, exc)


def sync_report_snapshot(
    *,
    report_type: str,
    report_date: date,
    context: dict,
    pdf_url: str | None = None,
) -> None:
    headers = _headers()
    if headers is None:
        return

    payload = {
        "report_type": report_type,
        "report_date": report_date.isoformat(),
        "context": context,
        "pdf_url": pdf_url,
    }
    try:
        response = httpx.post(
            f"{settings.supabase_url}/rest/v1/report_snapshot",
            headers=headers,
            json=payload,
            timeout=10,
        )
        response.raise_for_status()
    except httpx.HTTPError as exc:
        logger.warning(
            "report_snapshot 동기화 실패 (%s/%s): %s", report_type, report_date, exc
        )
