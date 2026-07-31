"""렌더링된 PDF를 Cloudflare R2(S3 호환)에 업로드한다.

R2 무료 티어(10GB, egress 비용 없음)가 Supabase Storage(1GB)보다 리포트
PDF 보관에 적합해서 선택했다. R2_* 환경변수가 없으면 조용히 스킵한다 —
R2는 Cloudflare 계정에서 대시보드로 한 번 수동 활성화해야 하는 상태라,
그 전까지는 로컬 서빙(reports_router의 PDF 스트리밍)만으로도 앱이 정상
동작해야 한다.
"""
from __future__ import annotations

import logging
from datetime import date

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from app.core.config import settings

logger = logging.getLogger("app.sync")


def _client():
    if not (settings.r2_account_id and settings.r2_access_key_id and settings.r2_secret_access_key):
        return None
    return boto3.client(
        "s3",
        endpoint_url=f"https://{settings.r2_account_id}.r2.cloudflarestorage.com",
        aws_access_key_id=settings.r2_access_key_id,
        aws_secret_access_key=settings.r2_secret_access_key,
        config=Config(signature_version="s3v4"),
        region_name="auto",
    )


def upload_report_pdf(report_type: str, report_date: date, pdf_bytes: bytes) -> str | None:
    """업로드에 성공하고 공개 URL을 만들 수 있으면 그 URL을, 아니면 None을 반환한다."""
    client = _client()
    if client is None or not settings.r2_bucket_name:
        return None

    key = f"{report_type}/{report_date.isoformat()}.pdf"
    try:
        client.put_object(
            Bucket=settings.r2_bucket_name,
            Key=key,
            Body=pdf_bytes,
            ContentType="application/pdf",
        )
    except (BotoCoreError, ClientError) as exc:
        logger.warning("R2 업로드 실패 (%s): %s", key, exc)
        return None

    if settings.r2_public_base_url:
        return f"{settings.r2_public_base_url.rstrip('/')}/{key}"
    return None
