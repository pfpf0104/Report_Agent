"""extraction_router.py의 업로드 검증(크기 제한, 매직 바이트)을 확인한다.

app.main을 통째로 임포트하면 weasyprint(reports_router 경유)가 딸려오는데,
weasyprint는 Windows에 GTK 네이티브 라이브러리가 없으면 임포트 자체가
실패한다 — extraction_router만 별도 FastAPI 앱에 마운트해 독립적으로 테스트한다.
"""
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routers import extraction_router

app = FastAPI()
app.include_router(extraction_router.router, prefix="/extraction")
client = TestClient(app)


def test_upload_rejects_non_pdf_extension_and_content_type():
    response = client.post(
        "/extraction/upload",
        files={"file": ("not_a_pdf.txt", b"hello world", "text/plain")},
    )
    assert response.status_code == 400


def test_upload_rejects_file_without_pdf_magic_bytes():
    """확장자와 Content-Type이 pdf라고 속여도, 실제 파일 시그니처가
    "%PDF-"로 시작하지 않으면 거부해야 한다."""
    fake_pdf = b"this is not really a pdf file, just renamed"
    response = client.post(
        "/extraction/upload",
        files={"file": ("fake.pdf", fake_pdf, "application/pdf")},
    )
    assert response.status_code == 400
    assert "유효한 PDF" in response.json()["detail"]


def test_upload_rejects_file_exceeding_size_limit(monkeypatch):
    monkeypatch.setattr(extraction_router, "_MAX_UPLOAD_SIZE_BYTES", 1024)  # 1KB로 낮춰서 테스트
    oversized = b"%PDF-1.4" + (b"x" * 2048)
    response = client.post(
        "/extraction/upload",
        files={"file": ("big.pdf", oversized, "application/pdf")},
    )
    assert response.status_code == 413


def test_upload_accepts_valid_pdf_magic_bytes():
    valid_pdf_like = b"%PDF-1.4\n%mock content for test\n"
    fake_document = type("Doc", (), {"id": 1, "status": "done"})()

    with patch.object(extraction_router, "ingest_pdf", new=AsyncMock(return_value=fake_document)):
        response = client.post(
            "/extraction/upload",
            files={"file": ("real.pdf", valid_pdf_like, "application/pdf")},
        )

    assert response.status_code == 200
    assert response.json() == {"document_id": 1, "status": "done"}
