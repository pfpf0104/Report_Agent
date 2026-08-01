"""PDF 업로드 → 텍스트/표 추출 → 숫자 후보 추출 → DB 저장 → Cross-check 검증까지
전 과정을 오케스트레이션한다.

RAW_UPLOAD_DIR에 원본 PDF를 보존한다(재처리·감사 목적). file_hash로 동일 파일
중복 업로드를 막는다.
"""
from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.orm import Session

from app.db.models.extracted_document import ExtractedDocument
from app.db.models.extracted_value import ExtractedValue
from app.extraction.number_extractor import extract_candidates
from app.extraction.pdf_parser import extract_document
from app.validation.checkers.internal_checkers import ALL_INTERNAL_CHECKER_FACTORIES
from app.validation.checkers.web_search_checker import UnconfiguredWebSearchProvider, WebSearchChecker
from app.validation.engine import ValidationEngine

logger = logging.getLogger("app.extraction.pipeline")

RAW_UPLOAD_DIR = Path(__file__).resolve().parents[2] / "data" / "pdf_uploads"


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _save_upload(filename: str, content: bytes) -> Path:
    RAW_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
    safe_name = Path(filename).name  # 경로 조작 방지
    dest = RAW_UPLOAD_DIR / f"{timestamp}_{safe_name}"
    dest.write_bytes(content)
    return dest


def _build_engine(company_name: str | None, bsns_year: int) -> ValidationEngine:
    internal_checkers = []
    if company_name:
        internal_checkers = [factory(company_name, bsns_year) for factory in ALL_INTERNAL_CHECKER_FACTORIES]
    external_checkers = [WebSearchChecker(UnconfiguredWebSearchProvider(), company_name=company_name)]
    return ValidationEngine(internal_checkers=internal_checkers, external_checkers=external_checkers)


async def ingest_pdf(
    db: Session,
    *,
    filename: str,
    content: bytes,
    company_name: str | None = None,
    bsns_year: int | None = None,
) -> ExtractedDocument:
    """PDF 1건을 자산화하고 각 숫자를 Cross-check까지 마친 뒤 결과를 커밋한다."""
    file_hash = _sha256(content)
    existing = db.query(ExtractedDocument).filter_by(file_hash=file_hash).first()
    if existing is not None:
        return existing

    storage_path = _save_upload(filename, content)
    document = ExtractedDocument(
        filename=filename,
        file_hash=file_hash,
        storage_path=str(storage_path),
        status="processing",
    )
    db.add(document)
    db.commit()
    db.refresh(document)

    try:
        parsed = extract_document(storage_path)
        document.page_count = parsed.page_count
        document.extraction_method = parsed.extraction_method

        candidates = extract_candidates(parsed)
        target_year = bsns_year or (datetime.now(timezone.utc).year - 1)
        engine = _build_engine(company_name, target_year)

        for candidate in candidates:
            status, results = await engine.validate(candidate)
            db.add(
                ExtractedValue(
                    document_id=document.id,
                    label=candidate.label,
                    value=candidate.value,
                    unit=candidate.unit,
                    page_number=candidate.page_number,
                    context_snippet=candidate.context_snippet,
                    extraction_confidence=candidate.extraction_confidence,
                    verification_status=status,
                    verification_details=[r.to_dict() for r in results],
                )
            )

        document.status = "done"
        document.processed_at = datetime.now(timezone.utc)
        db.commit()
    except Exception as exc:
        document.status = "failed"
        document.error_summary = f"{type(exc).__name__}: {exc}"[:2000]
        db.commit()
        raise

    db.refresh(document)
    return document
