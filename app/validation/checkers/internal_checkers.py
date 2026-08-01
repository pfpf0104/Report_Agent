"""내부 검증: 이미 연동된 DART/BOK/FRED/KIS 실데이터로 PDF 추출값을 대조한다.

각 체커는 라벨 문자열에 특정 키워드가 있을 때만 담당한다고 판단한다(예: "자본총계"
가 라벨에 있으면 DartCapitalTotalChecker가 응답). 회사명은 candidate.context_snippet
또는 라벨에서 함께 찾아야 하므로, 대상 회사 이름 목록을 생성자로 주입받는다 —
문서 전체가 어느 회사에 대한 것인지는 상위 엔진이 결정해 넘겨준다.
"""
from __future__ import annotations

import httpx

from app.extraction.number_extractor import ExtractedValueCandidate
from app.ingestion.connectors.dart_client import DartApiError, extract_capital_total, fetch_corp_code_map, fetch_single_company_financials
from app.validation.checkers.base import BaseChecker, CheckResult, compare_with_tolerance

_CAPITAL_TOTAL_LABELS = ("자본총계", "자본 총계", "total equity", "total capital")


class DartCapitalTotalChecker(BaseChecker):
    """라벨이 "자본총계" 계열이면 DART 최신 사업보고서 자본총계와 대조한다."""

    name = "dart_capital_total"
    source = "internal"

    def __init__(self, company_name: str, bsns_year: int):
        self.company_name = company_name
        self.bsns_year = bsns_year

    async def check(self, candidate: ExtractedValueCandidate) -> CheckResult:
        label_lower = candidate.label.lower()
        if not any(kw.lower() in label_lower for kw in _CAPITAL_TOTAL_LABELS):
            return CheckResult(checker=self.name, source=self.source, status="not_applicable")

        try:
            async with httpx.AsyncClient() as client:
                corp_map = await fetch_corp_code_map(client)
                corp_code = corp_map.get(self.company_name)
                if corp_code is None:
                    return CheckResult(
                        checker=self.name,
                        source=self.source,
                        status="check_failed",
                        detail=f"DART corp_code를 찾지 못함: {self.company_name}",
                    )
                accounts = await fetch_single_company_financials(client, corp_code, self.bsns_year)
        except (DartApiError, httpx.HTTPError) as exc:
            return CheckResult(
                checker=self.name, source=self.source, status="check_failed", detail=f"{type(exc).__name__}: {exc}"
            )

        reference = extract_capital_total(accounts)
        if reference is None:
            return CheckResult(
                checker=self.name, source=self.source, status="check_failed", detail="DART 응답에 자본총계 계정 없음"
            )

        ok, diff_pct = compare_with_tolerance(candidate.value, reference)
        return CheckResult(
            checker=self.name,
            source=self.source,
            status="verified" if ok else "mismatch",
            matched_value=reference,
            diff_pct=diff_pct,
            detail=f"DART {self.bsns_year}년 사업보고서 자본총계와 대조",
        )


ALL_INTERNAL_CHECKER_FACTORIES = (DartCapitalTotalChecker,)
