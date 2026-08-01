from unittest.mock import MagicMock, patch

from app.extraction.pdf_parser import DocumentExtraction, PageExtraction, extract_document


def test_document_extraction_method_is_text_when_all_pages_have_text_layer():
    doc = DocumentExtraction(page_count=2, pages=[PageExtraction(1, "본문", method="text"), PageExtraction(2, "본문2", method="text")])
    assert doc.extraction_method == "text"


def test_document_extraction_method_is_ocr_when_all_pages_used_ocr():
    doc = DocumentExtraction(page_count=1, pages=[PageExtraction(1, "OCR 결과", method="ocr")])
    assert doc.extraction_method == "ocr"


def test_document_extraction_method_is_mixed_when_pages_differ():
    doc = DocumentExtraction(
        page_count=2, pages=[PageExtraction(1, "본문", method="text"), PageExtraction(2, "OCR", method="ocr")]
    )
    assert doc.extraction_method == "mixed"


def _mock_page(text: str, tables=None):
    page = MagicMock()
    page.extract_text.return_value = text
    page.extract_tables.return_value = tables or []
    return page


def test_page_with_sufficient_text_uses_text_method(tmp_path):
    pdf_path = tmp_path / "dummy.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 stub")

    mock_pdf = MagicMock()
    mock_pdf.pages = [_mock_page("이것은 충분히 긴 재무제표 본문 텍스트입니다")]
    mock_pdf.__enter__ = MagicMock(return_value=mock_pdf)
    mock_pdf.__exit__ = MagicMock(return_value=False)

    with patch("app.extraction.pdf_parser.pdfplumber.open", return_value=mock_pdf):
        result = extract_document(pdf_path)

    assert result.pages[0].method == "text"
    assert "재무제표" in result.pages[0].text


def test_page_with_insufficient_text_falls_back_to_ocr(tmp_path):
    pdf_path = tmp_path / "scanned.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 stub")

    mock_pdf = MagicMock()
    mock_pdf.pages = [_mock_page("")]  # 텍스트 레이어 없음 → OCR 폴백 대상
    mock_pdf.__enter__ = MagicMock(return_value=mock_pdf)
    mock_pdf.__exit__ = MagicMock(return_value=False)

    with patch("app.extraction.pdf_parser.pdfplumber.open", return_value=mock_pdf), patch(
        "app.extraction.pdf_parser._ocr_page", return_value="OCR로 읽은 텍스트"
    ) as mock_ocr:
        result = extract_document(pdf_path)

    mock_ocr.assert_called_once()
    assert result.pages[0].method == "ocr"
    assert result.pages[0].text == "OCR로 읽은 텍스트"
    assert result.pages[0].tables == []  # OCR 경로는 표 구조를 복원하지 않는다


def test_ocr_failure_does_not_raise_and_leaves_page_marked_as_ocr(tmp_path):
    pdf_path = tmp_path / "broken_scan.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 stub")

    mock_pdf = MagicMock()
    mock_pdf.pages = [_mock_page("")]
    mock_pdf.__enter__ = MagicMock(return_value=mock_pdf)
    mock_pdf.__exit__ = MagicMock(return_value=False)

    with patch("app.extraction.pdf_parser.pdfplumber.open", return_value=mock_pdf), patch(
        "app.extraction.pdf_parser._ocr_page", side_effect=RuntimeError("tesseract not installed")
    ):
        result = extract_document(pdf_path)  # 예외를 삼키고 빈 텍스트로 남겨야 한다

    assert result.pages[0].method == "ocr"
    assert result.pages[0].text == ""
