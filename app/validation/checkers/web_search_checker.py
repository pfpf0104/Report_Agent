"""외부 웹 검색 기반 Cross-check. 담당 커넥터(DART/BOK/FRED/KIS)가 커버하지
못하는 임의 숫자(타 산업군, 해외기업 등)를 검증하기 위한 범용 폴백 경로다.

WebSearchProvider는 추상 인터페이스만 정의한다 — 실제 구현체(Google Custom
Search, Bing Search API 등)는 API 키가 발급되면 이 인터페이스를 구현해 등록한다
(구현 전까지 프로바이더가 없으므로 이 체커는 항상 status="check_failed"를
반환하고, 그 사실이 검증 결과에 그대로 남아 "사람이 확인해야 할 값"으로
분류된다 — 조용히 건너뛰지 않는다).
"""
from __future__ import annotations

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass

from app.extraction.number_extractor import ExtractedValueCandidate
from app.validation.checkers.base import BaseChecker, CheckResult, compare_with_tolerance


@dataclass
class WebSearchResult:
    snippet: str
    url: str


class WebSearchProvider(ABC):
    """Google/Bing 등 실제 검색 API를 감싸는 어댑터가 구현해야 하는 인터페이스."""

    @abstractmethod
    async def search(self, query: str, *, num_results: int = 5) -> list[WebSearchResult]:
        raise NotImplementedError


class UnconfiguredWebSearchProvider(WebSearchProvider):
    """API 키가 아직 없을 때 쓰는 기본 프로바이더. 항상 빈 결과를 낸다."""

    async def search(self, query: str, *, num_results: int = 5) -> list[WebSearchResult]:
        return []


_NUMBER_NEAR_LABEL_PATTERN = re.compile(r"-?[\d,]+(?:\.\d+)?")


def _extract_candidate_numbers(snippets: list[str]) -> list[float]:
    numbers = []
    for snippet in snippets:
        for raw in _NUMBER_NEAR_LABEL_PATTERN.findall(snippet):
            cleaned = raw.replace(",", "")
            try:
                numbers.append(float(cleaned))
            except ValueError:
                continue
    return numbers


class WebSearchChecker(BaseChecker):
    """모든 라벨에 대해 담당 가능(not_applicable을 반환하지 않음) — 다른 내부
    체커가 커버하지 못한 값들의 최종 폴백으로 쓰인다. 검색 스니펫에서 후보
    숫자를 뽑아 허용오차 이내로 일치하는 게 하나라도 있으면 verified로 본다.
    자동 확정이 아니라 "참고용 대조"이므로 detail에 근거 URL을 남겨 사람이
    최종 판단하도록 한다.
    """

    name = "web_search_cross_check"
    source = "external"

    def __init__(self, provider: WebSearchProvider, company_name: str | None = None):
        self.provider = provider
        self.company_name = company_name

    async def check(self, candidate: ExtractedValueCandidate) -> CheckResult:
        query_parts = [p for p in (self.company_name, candidate.label) if p]
        query = " ".join(query_parts)
        if not query:
            return CheckResult(checker=self.name, source=self.source, status="not_applicable")

        results = await self.provider.search(query)
        if not results:
            return CheckResult(
                checker=self.name,
                source=self.source,
                status="check_failed",
                detail="웹 검색 프로바이더 미구성 또는 결과 없음 — 사람이 직접 확인 필요",
            )

        candidate_numbers = _extract_candidate_numbers([r.snippet for r in results])
        for number in candidate_numbers:
            ok, diff_pct = compare_with_tolerance(candidate.value, number, tolerance_pct=2.0)
            if ok:
                matching_urls = [r.url for r in results if str(number).replace(".0", "") in r.snippet]
                return CheckResult(
                    checker=self.name,
                    source=self.source,
                    status="verified",
                    matched_value=number,
                    diff_pct=diff_pct,
                    detail=f"웹 검색 근거: {matching_urls[:1] or [r.url for r in results[:1]]}",
                )

        return CheckResult(
            checker=self.name,
            source=self.source,
            status="mismatch",
            detail=f"검색 결과 {len(results)}건에서 일치하는 값을 찾지 못함 — 사람이 직접 확인 필요",
        )
