"""페이지 텍스트/표에서 "라벨 + 숫자(+단위)" 후보를 뽑아 ExtractedValue 레코드용
데이터로 만든다.

두 경로를 쓴다:
  1. 표(tables) — 각 행을 "첫 컬럼=라벨, 나머지 컬럼 중 숫자로 파싱되는 첫 값=value"로
     본다. 재무제표류 PDF는 대부분 이 경로로 커버된다. 신뢰도가 높다(0.9).
  2. 자유 텍스트(text) — "라벨: 123,456" 또는 "라벨 123,456원" 같은 패턴을
     정규식으로 찾는다. 표가 없는 문서나 OCR 경로에서 쓰인다. 신뢰도가 낮다(0.6).

콤마 천단위 구분자, 괄호로 음수 표기((1,234) → -1234), 단위 접미사(원/천원/
백만원/%)를 인식한다. 통화기호나 순수 페이지번호처럼 명백히 데이터가 아닌
값은 최대한 걸러내지만, 완벽하지 않으므로 extraction_confidence를 함께 남겨
사람이 낮은 신뢰도 값을 우선 검토할 수 있게 한다.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from app.extraction.pdf_parser import DocumentExtraction

_UNIT_MULTIPLIERS = {
    "원": 1,
    "천원": 1_000,
    "백만원": 1_000_000,
    "억원": 100_000_000,
    "%": 1,  # 퍼센트는 배율 변환 대상이 아니라 그대로 둔다(단위 표시만 %)
}

# FnGuide류 PDF는 섹션 헤더에 "단위 : 억원" 같은 문구로 그 섹션 전체의 배율을
# 선언한다(예: "포괄손익계산서 [연간] 단위 : 억원"). 이 문구를 못 찾으면 값을
# 원 단위 그대로로 취급해, DART 등 원 단위 소스와 대조할 때 조용히 자릿수가
# 어긋나는 실제 버그를 겪었다 — 반드시 명시적으로 배율을 곱해 정규화한다.
_UNIT_DECLARATION_PATTERN = re.compile(r"단위\s*[:：]\s*(억원|백만원|천원|원)")

_NUMBER_TOKEN = r"\(?-?[\d,]+(?:\.\d+)?\)?%?"
_LINE_PATTERN = re.compile(
    rf"^(?P<label>[^\d:：]{{1,60}}?)[:\s：]+(?P<value>{_NUMBER_TOKEN})\s*(?P<unit>천원|백만원|억원|원)?\s*$"
)
_TABLE_NUMBER_PATTERN = re.compile(rf"^{_NUMBER_TOKEN}$")

# FnGuide류 재무제표 PDF는 "라벨 값1 값2 값3 값4 값5 값6" 처럼 한 줄에 여러 회계기간
# 값을 공백으로 나열한다(예: "매출액(수익) 327,657 661,930 971,467 ..."). _LINE_PATTERN은
# 단일값만 잡으므로, 라벨 뒤에 숫자 토큰이 2개 이상 이어지는 라인은 별도로 처리한다.
_MULTI_VALUE_LINE_PATTERN = re.compile(
    rf"^(?P<label>[^\d:：]{{1,60}}?)\s+(?P<values>(?:{_NUMBER_TOKEN}\s*)+)$"
)
_VALUE_TOKEN_PATTERN = re.compile(_NUMBER_TOKEN)


@dataclass
class ExtractedValueCandidate:
    label: str
    value: float
    unit: str | None
    page_number: int
    context_snippet: str
    extraction_confidence: float


def _parse_number(raw: str) -> float | None:
    raw = raw.strip()
    if not raw:
        return None
    negative = raw.startswith("(") and raw.endswith(")")
    cleaned = raw.strip("()%").replace(",", "")
    if not cleaned or not re.fullmatch(r"-?\d+(\.\d+)?", cleaned):
        return None
    value = float(cleaned)
    return -value if negative else value


def _unit_multiplier(unit_label: str | None) -> float:
    if unit_label is None:
        return 1.0
    return float(_UNIT_MULTIPLIERS.get(unit_label, 1))


def _from_tables(
    page_number: int, tables: list[list[list[str | None]]], *, declared_unit: str | None
) -> list[ExtractedValueCandidate]:
    multiplier = _unit_multiplier(declared_unit)
    candidates = []
    for table in tables:
        for row in table:
            if not row or len(row) < 2:
                continue
            label = (row[0] or "").strip()
            if not label or len(label) > 80:
                continue
            for cell in row[1:]:
                if not cell:
                    continue
                cell = cell.strip()
                if not _TABLE_NUMBER_PATTERN.fullmatch(cell):
                    continue
                value = _parse_number(cell)
                if value is None:
                    continue
                is_percent = cell.endswith("%")
                candidates.append(
                    ExtractedValueCandidate(
                        label=label,
                        value=value if is_percent else value * multiplier,
                        unit="%" if is_percent else declared_unit,
                        page_number=page_number,
                        context_snippet=" | ".join(c or "" for c in row),
                        extraction_confidence=0.9,
                    )
                )
                break  # 행당 첫 번째 유효 숫자만 대표값으로 취한다(당기/전기 다열 표는 향후 개선 대상)
    return candidates


def _from_multi_value_line(page_number: int, line: str, *, multiplier: float) -> list[ExtractedValueCandidate]:
    """"라벨 값1 값2 값3 ..." 형태(FnGuide 재무제표 등)를 각 기간값별 후보로 분리한다.

    기간 라벨(연도/분기)까지는 헤더 행을 별도로 추적해야 알 수 있어 이번 버전에서는
    붙이지 않는다 — 대신 라벨에 "(N번째 값)" 순번을 남겨 같은 라벨의 값들을 구분하고,
    사람이 context_snippet(원문 라인 전체)을 보고 어느 기간인지 확인할 수 있게 한다.
    """
    match = _MULTI_VALUE_LINE_PATTERN.match(line.strip())
    if not match:
        return []
    label = match.group("label").strip()
    if not label or len(label) < 2:
        return []
    tokens = _VALUE_TOKEN_PATTERN.findall(match.group("values"))
    if len(tokens) < 2:  # 값이 1개뿐이면 _from_text의 단일값 경로가 이미 처리한다
        return []

    candidates = []
    for i, token in enumerate(tokens, start=1):
        value = _parse_number(token)
        if value is None:
            continue
        is_percent = token.endswith("%")
        candidates.append(
            ExtractedValueCandidate(
                label=f"{label} ({i}번째 값)",
                value=value if is_percent else value * multiplier,
                unit="%" if is_percent else None,
                page_number=page_number,
                context_snippet=line.strip(),
                extraction_confidence=0.5,  # 기간 매핑이 없어 단일값 라인보다 신뢰도를 낮게 잡는다
            )
        )
    return candidates


def _from_text(page_number: int, text: str) -> list[ExtractedValueCandidate]:
    """라인을 순서대로 훑으며 "단위 : 억원" 선언을 만나면 그 이후 라인들에 배율을
    적용한다. 한 페이지 안에 여러 섹션(손익계산서/재무상태표/현금흐름표)이 서로
    다른 단위를 선언할 수 있어, 최신 선언값을 계속 갱신하며 진행한다.
    """
    candidates = []
    current_multiplier = 1.0
    for line in text.splitlines():
        stripped = line.strip()

        unit_match = _UNIT_DECLARATION_PATTERN.search(stripped)
        if unit_match:
            current_multiplier = _unit_multiplier(unit_match.group(1))

        match = _LINE_PATTERN.match(stripped)
        if match:
            label = match.group("label").strip()
            raw_value_token = match.group("value")
            value = _parse_number(raw_value_token)
            if value is None or not label:
                continue
            unit = match.group("unit")
            is_percent = raw_value_token.endswith("%")
            # 라인 자체에 명시적 단위 접미사(예: "123,456 억원")가 있으면 그걸 우선한다.
            # %는 배율 변환 대상이 아니다(표/멀티값 경로와 동일 규칙).
            multiplier = 1.0 if is_percent else (_unit_multiplier(unit) if unit else current_multiplier)
            candidates.append(
                ExtractedValueCandidate(
                    label=label,
                    value=value * multiplier,
                    unit="%" if is_percent else unit,
                    page_number=page_number,
                    context_snippet=stripped,
                    extraction_confidence=0.6,
                )
            )
            continue

        candidates.extend(_from_multi_value_line(page_number, stripped, multiplier=current_multiplier))
    return candidates


def extract_candidates(document: DocumentExtraction) -> list[ExtractedValueCandidate]:
    """페이지별로 표 경로를 먼저 뽑고, 텍스트 경로는 표에서 이미 잡힌 라벨을 제외한다.

    FnGuide 재무제표류 PDF는 같은 항목(예: "매출액(수익)")이 표 셀에도, 그 표를
    구성하는 원본 텍스트 라인에도 그대로 나타난다. 표 경로가 신뢰도(0.9)도 더
    높고 컬럼 구조도 보존하므로, 텍스트 경로는 표에 없던 라벨만 보완적으로 채운다.

    표는 셀 자체에 단위 표기가 없으므로, 같은 페이지 텍스트에서 "단위 : 억원"
    선언을 찾아 표 전체에 적용한다(페이지 안에 단위가 섞이는 경우는 흔치 않다는
    전제 — 흔하다고 밝혀지면 표별로 가장 가까운 헤더를 찾도록 개선 필요).
    """
    candidates: list[ExtractedValueCandidate] = []
    for page in document.pages:
        unit_matches = _UNIT_DECLARATION_PATTERN.findall(page.text)
        page_unit = unit_matches[0] if unit_matches else None

        table_candidates = _from_tables(page.page_number, page.tables, declared_unit=page_unit) if page.tables else []
        table_labels = {c.label for c in table_candidates}
        candidates.extend(table_candidates)

        for text_candidate in _from_text(page.page_number, page.text):
            base_label = text_candidate.label.split(" (")[0]
            if base_label in table_labels:
                continue
            candidates.append(text_candidate)
    return candidates
