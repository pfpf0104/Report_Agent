"""Cross-check 체커 공통 인터페이스.

체커는 ExtractedValueCandidate 하나를 받아 CheckResult를 반환한다. 체커가
"이 라벨은 내가 담당하는 범위가 아니다"라고 판단하면 status="not_applicable"을
반환해 엔진이 다음 체커로 넘어가게 한다 — 체커 자체의 실패(API 오류 등)와
구분하기 위함이다(그건 status="check_failed").
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal

from app.extraction.number_extractor import ExtractedValueCandidate

CheckStatus = Literal["verified", "mismatch", "not_applicable", "check_failed"]

# 값 비교 허용 오차. PDF 추출은 반올림·단위 표기 오차가 흔해 상대오차 기준으로 본다.
DEFAULT_TOLERANCE_PCT = 1.0


@dataclass
class CheckResult:
    checker: str
    source: Literal["internal", "external"]
    status: CheckStatus
    matched_value: float | None = None
    diff_pct: float | None = None
    detail: str = ""
    checked_at: str = ""

    def __post_init__(self) -> None:
        if not self.checked_at:
            self.checked_at = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> dict:
        return {
            "checker": self.checker,
            "source": self.source,
            "status": self.status,
            "matched_value": self.matched_value,
            "diff_pct": self.diff_pct,
            "detail": self.detail,
            "checked_at": self.checked_at,
        }


def compare_with_tolerance(
    extracted: float, reference: float, *, tolerance_pct: float = DEFAULT_TOLERANCE_PCT
) -> tuple[bool, float]:
    """상대오차(%)를 계산해 허용치 이내인지 판정한다. reference=0이면 절대오차로 폴백."""
    if reference == 0:
        diff_pct = abs(extracted - reference) * 100
    else:
        diff_pct = abs(extracted - reference) / abs(reference) * 100
    return diff_pct <= tolerance_pct, diff_pct


class BaseChecker(ABC):
    name: str
    source: Literal["internal", "external"]

    @abstractmethod
    async def check(self, candidate: ExtractedValueCandidate) -> CheckResult:
        """candidate가 이 체커의 담당 범위 밖이면 status="not_applicable"을 반환한다."""
        raise NotImplementedError
