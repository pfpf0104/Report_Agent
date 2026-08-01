"""PDF 페이지에서 텍스트를 뽑는다. 텍스트 레이어가 있으면 pdfplumber로 바로 뽑고,
없으면(스캔본) OCR(pytesseract)로 폴백한다.

텍스트 추출 성공 여부는 "글자 수가 임계치 이상인가"로 판단한다 — 텍스트 레이어가
없는 스캔 PDF는 pdfplumber가 빈 문자열이나 공백만 반환하기 때문이다. 표 구조는
pdfplumber의 extract_tables()로 별도 추출해 숫자 파싱 정확도를 높인다(OCR
경로에서는 표 구조를 얻을 수 없어 텍스트만 반환한다).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

import pdfplumber

logger = logging.getLogger("app.extraction")

MIN_CHARS_FOR_TEXT_LAYER = 20  # 이보다 적으면 텍스트 레이어가 없다고 판단해 OCR로 폴백


@dataclass
class PageExtraction:
    page_number: int  # 1-based
    text: str
    tables: list[list[list[str | None]]] = field(default_factory=list)
    method: str = "text"  # text | ocr


@dataclass
class DocumentExtraction:
    page_count: int
    pages: list[PageExtraction]

    @property
    def extraction_method(self) -> str:
        methods = {p.method for p in self.pages}
        if methods == {"text"}:
            return "text"
        if methods == {"ocr"}:
            return "ocr"
        return "mixed"


def _ocr_page(pdf_path: Path, page_number: int) -> str:
    """pdf2image로 해당 페이지만 이미지로 렌더링해 pytesseract로 OCR한다.

    페이지 단위로 렌더링하는 이유: 문서 전체를 한 번에 이미지화하면 텍스트
    레이어가 있는 페이지까지 불필요하게 OCR 대상이 되어 느려지고, 대용량
    문서에서는 메모리도 크게 쓴다.
    """
    import pdf2image
    import pytesseract

    images = pdf2image.convert_from_path(str(pdf_path), first_page=page_number, last_page=page_number)
    if not images:
        return ""
    return pytesseract.image_to_string(images[0], lang="kor+eng")


def extract_document(pdf_path: Path) -> DocumentExtraction:
    pages: list[PageExtraction] = []
    with pdfplumber.open(pdf_path) as pdf:
        page_count = len(pdf.pages)
        for i, page in enumerate(pdf.pages, start=1):
            text = page.extract_text() or ""
            tables = [t for t in (page.extract_tables() or [])]

            if len(text.strip()) < MIN_CHARS_FOR_TEXT_LAYER:
                logger.info("페이지 %d: 텍스트 레이어 부족(%d자) — OCR로 폴백", i, len(text.strip()))
                try:
                    text = _ocr_page(pdf_path, i)
                    method = "ocr"
                except Exception:
                    logger.exception("페이지 %d OCR 실패 — 빈 텍스트로 남김", i)
                    method = "ocr"
                tables = []  # OCR 경로는 표 구조를 복원하지 못한다
            else:
                method = "text"

            pages.append(PageExtraction(page_number=i, text=text, tables=tables, method=method))

    return DocumentExtraction(page_count=page_count, pages=pages)
