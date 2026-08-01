"""여러 체커를 순서대로 실행해 ExtractedValueCandidate 하나의 최종 검증 상태를
정한다.

우선순위: 내부 체커(DART 등, 정확도 높음) 먼저 시도 → 하나라도 verified/mismatch를
내면 그걸로 확정하고 나머지 내부 체커는 건너뛴다(같은 값을 두 번 검증할 필요 없음).
모든 내부 체커가 not_applicable/check_failed면 외부(웹 검색) 체커로 폴백한다.

최종 verification_status 판정 규칙:
  - 어떤 체커든 "mismatch"를 냈으면 → mismatch (가장 강한 신호, 최우선)
  - "verified"를 낸 체커가 있으면 → verified
  - 전부 not_applicable/check_failed면 → unverified (사람이 확인해야 할 값)
"""
from __future__ import annotations

from app.extraction.number_extractor import ExtractedValueCandidate
from app.validation.checkers.base import BaseChecker, CheckResult


class ValidationEngine:
    def __init__(self, internal_checkers: list[BaseChecker], external_checkers: list[BaseChecker]):
        self.internal_checkers = internal_checkers
        self.external_checkers = external_checkers

    async def validate(self, candidate: ExtractedValueCandidate) -> tuple[str, list[CheckResult]]:
        results: list[CheckResult] = []

        for checker in self.internal_checkers:
            result = await checker.check(candidate)
            results.append(result)
            if result.status in ("verified", "mismatch"):
                return self._finalize(results)

        for checker in self.external_checkers:
            result = await checker.check(candidate)
            results.append(result)
            if result.status in ("verified", "mismatch"):
                return self._finalize(results)

        return self._finalize(results)

    @staticmethod
    def _finalize(results: list[CheckResult]) -> tuple[str, list[CheckResult]]:
        statuses = {r.status for r in results}
        if "mismatch" in statuses:
            return "mismatch", results
        if "verified" in statuses:
            return "verified", results
        if statuses and statuses <= {"check_failed"}:
            return "check_failed", results
        return "unverified", results
