import pytest

from app.extraction.number_extractor import ExtractedValueCandidate
from app.validation.checkers.base import BaseChecker, CheckResult, compare_with_tolerance
from app.validation.engine import ValidationEngine


def _candidate(value: float = 300_000.0) -> ExtractedValueCandidate:
    return ExtractedValueCandidate(
        label="자본총계",
        value=value,
        unit=None,
        page_number=1,
        context_snippet="자본총계 300,000",
        extraction_confidence=0.9,
    )


class _StubChecker(BaseChecker):
    def __init__(self, name: str, source, result: CheckResult):
        self.name = name
        self.source = source
        self._result = result
        self.called = False

    async def check(self, candidate: ExtractedValueCandidate) -> CheckResult:
        self.called = True
        return self._result


def test_compare_with_tolerance_within_bounds():
    ok, diff_pct = compare_with_tolerance(300_000, 300_100, tolerance_pct=1.0)
    assert ok
    assert diff_pct < 1.0


def test_compare_with_tolerance_outside_bounds():
    ok, diff_pct = compare_with_tolerance(300_000, 500_000, tolerance_pct=1.0)
    assert not ok
    assert diff_pct > 1.0


def test_compare_with_tolerance_zero_reference_uses_absolute_diff():
    ok, diff_pct = compare_with_tolerance(0.5, 0, tolerance_pct=1.0)
    assert not ok
    assert diff_pct == 50.0


async def test_internal_verified_short_circuits_remaining_checkers():
    verified = _StubChecker(
        "internal_a", "internal", CheckResult(checker="internal_a", source="internal", status="verified")
    )
    unreached = _StubChecker(
        "internal_b", "internal", CheckResult(checker="internal_b", source="internal", status="verified")
    )
    engine = ValidationEngine(internal_checkers=[verified, unreached], external_checkers=[])
    status, results = await engine.validate(_candidate())

    assert status == "verified"
    assert len(results) == 1
    assert unreached.called is False


async def test_not_applicable_falls_through_to_next_internal_checker():
    skip = _StubChecker(
        "internal_a", "internal", CheckResult(checker="internal_a", source="internal", status="not_applicable")
    )
    hit = _StubChecker(
        "internal_b", "internal", CheckResult(checker="internal_b", source="internal", status="mismatch")
    )
    engine = ValidationEngine(internal_checkers=[skip, hit], external_checkers=[])
    status, results = await engine.validate(_candidate())

    assert status == "mismatch"
    assert len(results) == 2


async def test_falls_back_to_external_when_all_internal_not_applicable():
    internal = _StubChecker(
        "internal_a", "internal", CheckResult(checker="internal_a", source="internal", status="not_applicable")
    )
    external = _StubChecker(
        "web_search", "external", CheckResult(checker="web_search", source="external", status="verified")
    )
    engine = ValidationEngine(internal_checkers=[internal], external_checkers=[external])
    status, results = await engine.validate(_candidate())

    assert status == "verified"
    assert external.called is True


async def test_all_check_failed_yields_check_failed_status():
    failed = _StubChecker(
        "web_search", "external", CheckResult(checker="web_search", source="external", status="check_failed")
    )
    engine = ValidationEngine(internal_checkers=[], external_checkers=[failed])
    status, results = await engine.validate(_candidate())

    assert status == "check_failed"


async def test_no_checkers_at_all_yields_unverified():
    engine = ValidationEngine(internal_checkers=[], external_checkers=[])
    status, results = await engine.validate(_candidate())

    assert status == "unverified"
    assert results == []


async def test_mismatch_wins_over_verified_when_both_present():
    # 순서상 mismatch가 먼저 나와도 결과 자체는 하나로 확정된다(mismatch는 항상
    # 즉시 반환되므로 실제로는 이 케이스가 엔진 흐름상 나오지 않지만, _finalize의
    # 우선순위 규칙 자체를 직접 검증한다).
    results = [
        CheckResult(checker="a", source="internal", status="verified"),
        CheckResult(checker="b", source="external", status="mismatch"),
    ]
    status, _ = ValidationEngine._finalize(results)
    assert status == "mismatch"
