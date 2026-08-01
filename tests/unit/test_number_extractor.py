from app.extraction.number_extractor import extract_candidates
from app.extraction.pdf_parser import DocumentExtraction, PageExtraction


def _doc(pages: list[PageExtraction]) -> DocumentExtraction:
    return DocumentExtraction(page_count=len(pages), pages=pages)


def test_table_row_extracts_label_and_first_numeric_cell():
    page = PageExtraction(
        page_number=1,
        text="",
        tables=[[["자본총계", "300,000", "280,000"]]],
    )
    candidates = extract_candidates(_doc([page]))
    assert len(candidates) == 1
    assert candidates[0].label == "자본총계"
    assert candidates[0].value == 300_000.0
    assert candidates[0].extraction_confidence == 0.9


def test_table_cell_with_parentheses_is_negative():
    page = PageExtraction(page_number=1, text="", tables=[[["영업손실", "(5,334)"]]])
    candidates = extract_candidates(_doc([page]))
    assert candidates[0].value == -5334.0


def test_single_value_text_line_is_extracted():
    page = PageExtraction(page_number=1, text="매출액: 123,456원", tables=[])
    candidates = extract_candidates(_doc([page]))
    assert len(candidates) == 1
    assert candidates[0].label == "매출액"
    assert candidates[0].value == 123_456.0
    assert candidates[0].unit == "원"


def test_multi_value_line_splits_into_indexed_candidates():
    page = PageExtraction(
        page_number=1, text="매출액(수익) 327,657 661,930 971,467", tables=[]
    )
    candidates = extract_candidates(_doc([page]))
    labels = {c.label: c.value for c in candidates}
    assert labels["매출액(수익) (1번째 값)"] == 327_657.0
    assert labels["매출액(수익) (2번째 값)"] == 661_930.0
    assert labels["매출액(수익) (3번째 값)"] == 971_467.0


def test_text_candidate_skipped_when_label_already_in_table():
    page = PageExtraction(
        page_number=1,
        text="자본총계 300,000",
        tables=[[["자본총계", "300,000"]]],
    )
    candidates = extract_candidates(_doc([page]))
    # 표 경로에서 이미 "자본총계"를 잡았으므로 텍스트 경로의 단일값 라인은 걸러진다.
    assert len(candidates) == 1
    assert candidates[0].extraction_confidence == 0.9


def test_unit_declaration_scales_subsequent_table_values():
    text = "포괄손익계산서 [연간] 단위 : 억원"
    page = PageExtraction(page_number=1, text=text, tables=[[["자본총계", "535,038"]]])
    candidates = extract_candidates(_doc([page]))
    assert candidates[0].value == 535_038 * 100_000_000
    assert candidates[0].unit == "억원"


def test_unit_declaration_scales_subsequent_text_lines():
    text = "단위 : 백만원\n매출액: 123,456"
    page = PageExtraction(page_number=1, text=text, tables=[])
    candidates = extract_candidates(_doc([page]))
    assert candidates[0].value == 123_456 * 1_000_000


def test_percent_values_are_never_scaled_by_unit_declaration():
    text = "단위 : 억원\n영업이익률 12.5%"
    page = PageExtraction(page_number=1, text=text, tables=[])
    candidates = extract_candidates(_doc([page]))
    assert candidates[0].value == 12.5
    assert candidates[0].unit == "%"


def test_no_numbers_produces_no_candidates():
    page = PageExtraction(page_number=1, text="이 페이지에는 숫자가 없습니다", tables=[])
    assert extract_candidates(_doc([page])) == []
