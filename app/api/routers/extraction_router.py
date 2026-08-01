"""PDF 업로드 → 자산화 → Cross-check 검증 결과 조회 API.

응답에서 검증값/미검증값을 명확히 구분해 사람이 어떤 값을 직접 확인해야
하는지 바로 알 수 있게 한다(verified vs unverified/mismatch/check_failed).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.db.base import get_db
from app.db.models.extracted_document import ExtractedDocument
from app.db.models.extracted_value import ExtractedValue
from app.extraction.pipeline import ingest_pdf

router = APIRouter()

_NEEDS_REVIEW_STATUSES = ("unverified", "mismatch", "check_failed")

# 임의로 큰 PDF가 업로드돼 메모리를 고갈시키는 걸 막는다. FnGuide류 재무제표
# PDF는 수백 KB~수 MB대라 50MB면 실사용 사례를 넉넉히 커버한다.
_MAX_UPLOAD_SIZE_BYTES = 50 * 1024 * 1024
# PDF 매직 넘버("%PDF-"). Content-Type/확장자는 클라이언트가 임의로 보내는
# 값이라 신뢰할 수 없다 — 실제 파일 내용으로 한 번 더 검증한다.
_PDF_MAGIC_BYTES = b"%PDF-"


def _value_to_dict(value: ExtractedValue) -> dict:
    return {
        "id": value.id,
        "label": value.label,
        "value": float(value.value),
        "unit": value.unit,
        "page_number": value.page_number,
        "context_snippet": value.context_snippet,
        "extraction_confidence": float(value.extraction_confidence) if value.extraction_confidence is not None else None,
        "verification_status": value.verification_status,
        "verification_details": value.verification_details,
    }


@router.post("/upload")
async def upload_pdf(
    file: UploadFile = File(...),
    company_name: str | None = None,
    bsns_year: int | None = None,
    db: Session = Depends(get_db),
) -> dict:
    if file.content_type != "application/pdf" and not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="PDF 파일만 업로드할 수 있다")

    # 크기 제한을 넘으면 전체를 다 읽지 않고 즉시 끊는다 — file.read()로 전체를
    # 받은 뒤 길이를 재는 방식은 이미 메모리에 다 올라온 뒤라 제한의 의미가 없다.
    chunks: list[bytes] = []
    total_size = 0
    while True:
        chunk = await file.read(1024 * 1024)
        if not chunk:
            break
        total_size += len(chunk)
        if total_size > _MAX_UPLOAD_SIZE_BYTES:
            raise HTTPException(
                status_code=413,
                detail=f"파일이 너무 크다(최대 {_MAX_UPLOAD_SIZE_BYTES // (1024*1024)}MB)",
            )
        chunks.append(chunk)
    content = b"".join(chunks)

    # Content-Type/확장자는 클라이언트가 보내는 값이라 신뢰할 수 없다 —
    # 실제 파일 시그니처(매직 바이트)로 한 번 더 검증한다.
    if not content.startswith(_PDF_MAGIC_BYTES):
        raise HTTPException(status_code=400, detail="유효한 PDF 파일이 아니다")

    document = await ingest_pdf(
        db, filename=file.filename, content=content, company_name=company_name, bsns_year=bsns_year
    )
    return {"document_id": document.id, "status": document.status}


@router.get("/documents/{document_id}")
def get_document_result(document_id: int, db: Session = Depends(get_db)) -> dict:
    document = db.query(ExtractedDocument).filter_by(id=document_id).first()
    if document is None:
        raise HTTPException(status_code=404, detail="문서를 찾을 수 없다")

    values = db.query(ExtractedValue).filter_by(document_id=document_id).all()
    verified = [v for v in values if v.verification_status == "verified"]
    needs_review = [v for v in values if v.verification_status in _NEEDS_REVIEW_STATUSES]

    return {
        "document": {
            "id": document.id,
            "filename": document.filename,
            "status": document.status,
            "extraction_method": document.extraction_method,
            "page_count": document.page_count,
            "error_summary": document.error_summary,
        },
        "summary": {
            "total_values": len(values),
            "verified_count": len(verified),
            "needs_review_count": len(needs_review),
        },
        # 사람이 바로 훑어볼 수 있도록 검증됨/확인 필요 목록을 분리해서 반환한다.
        "verified_values": [_value_to_dict(v) for v in verified],
        "needs_review_values": [_value_to_dict(v) for v in needs_review],
    }
