"""GIPS Composite Report 표 생성 — 제거된 성과 페이지를 복원하기 위한 근거.

`performance_disclosure.py`가 "이 요건을 충족하면 성과를 다시 실을 수 있다"고
선언한 세 가지를 실제로 계산한다:

  1. 최소 5년 연간 수익률(설정 이후 5년 미만이면 전 기간)
  2. 동일 기간 벤치마크 수익률 병기
  3. 각 연도 말 기준 3년 연환산 사후(ex-post) 표준편차

출처: GIPS Standards for Firms 2020 (references/README.md).

## 3년 표준편차의 함정

GIPS는 각 연도 말 기준 **직전 36개월**의 표준편차를 요구한다. 36개월이 안 되는
연도는 계산하지 않고 비워둔다 — 12개월치로 계산해 "3년 표준편차"라고 표기하면
표는 채워지지만 라벨이 거짓말이 된다. 이 모듈은 그런 연도에 None을 남긴다.

## 입력

이 모듈은 DB를 모른다. 날짜-수익률 쌍을 받아 계산만 한다. 실데이터 연결은
호출부(Phase 0 백필 이후)의 몫이다.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from app.computation.risk.metrics import annualized_return, annualized_volatility

MONTHS_REQUIRED_FOR_3YR_STDEV = 36


@dataclass(frozen=True)
class GipsYearRow:
    year: int
    portfolio_return: float
    benchmark_return: float | None
    portfolio_3yr_stdev: float | None
    benchmark_3yr_stdev: float | None
    months_in_year: int

    @property
    def excess_return(self) -> float | None:
        if self.benchmark_return is None:
            return None
        return self.portfolio_return - self.benchmark_return

    @property
    def is_partial_year(self) -> bool:
        return self.months_in_year < 12


def _group_by_year(observations: list[tuple[date, float]]) -> dict[int, list[float]]:
    grouped: dict[int, list[float]] = {}
    for d, r in sorted(observations):
        grouped.setdefault(d.year, []).append(r)
    return grouped


def _trailing_36m(observations: list[tuple[date, float]], year: int) -> list[float] | None:
    """해당 연도 말 기준 직전 36개월 수익률. 부족하면 None."""
    upto = [r for d, r in sorted(observations) if d.year <= year]
    if len(upto) < MONTHS_REQUIRED_FOR_3YR_STDEV:
        return None
    return upto[-MONTHS_REQUIRED_FOR_3YR_STDEV:]


def build_gips_table(
    portfolio_monthly: list[tuple[date, float]],
    benchmark_monthly: list[tuple[date, float]] | None = None,
) -> list[GipsYearRow]:
    """월간 수익률 시계열 → 연도별 GIPS 표.

    portfolio_monthly: [(월말일자, 월간수익률 소수), ...]
    benchmark_monthly: 같은 형식. None이면 벤치마크 열이 비워진다 — 다만 GIPS는
        벤치마크 병기를 요구하므로, 실제 배포 리포트에서는 반드시 제공해야 한다.
    """
    if not portfolio_monthly:
        return []

    p_by_year = _group_by_year(portfolio_monthly)
    b_by_year = _group_by_year(benchmark_monthly) if benchmark_monthly else {}

    rows: list[GipsYearRow] = []
    for year in sorted(p_by_year):
        p_year = p_by_year[year]
        b_year = b_by_year.get(year)

        p_36 = _trailing_36m(portfolio_monthly, year)
        b_36 = _trailing_36m(benchmark_monthly, year) if benchmark_monthly else None

        rows.append(
            GipsYearRow(
                year=year,
                # 연간 수익률은 그 해 월수익률의 복리 — 연환산이 아니다.
                # 부분연도(예: 3개월만 있는 첫 해)를 연환산하면 실제로 벌지 않은
                # 수익을 표기하게 되므로 그대로 둔다.
                portfolio_return=annualized_return(p_year, periods_per_year=len(p_year)),
                benchmark_return=(
                    annualized_return(b_year, periods_per_year=len(b_year)) if b_year else None
                ),
                portfolio_3yr_stdev=(
                    annualized_volatility(p_36, periods_per_year=12) if p_36 else None
                ),
                benchmark_3yr_stdev=(
                    annualized_volatility(b_36, periods_per_year=12) if b_36 else None
                ),
                months_in_year=len(p_year),
            )
        )
    return rows


def meets_gips_minimum_history(rows: list[GipsYearRow], min_years: int = 5) -> bool:
    """GIPS 최소 이력(5년) 충족 여부. 부분연도는 세지 않는다."""
    return sum(1 for r in rows if not r.is_partial_year) >= min_years


def format_gips_table_rows(rows: list[GipsYearRow]) -> list[list[str]]:
    """리포트 템플릿용 문자열 행. 계산 불가한 칸은 '—'로 남긴다(0으로 채우지 않는다)."""
    def pct(v: float | None) -> str:
        return "—" if v is None else f"{v * 100:+.2f}%"

    def std(v: float | None) -> str:
        return "—" if v is None else f"{v * 100:.2f}%"

    out = []
    for r in rows:
        label = f"{r.year}*" if r.is_partial_year else str(r.year)
        out.append([
            label,
            pct(r.portfolio_return),
            pct(r.benchmark_return),
            pct(r.excess_return),
            std(r.portfolio_3yr_stdev),
            std(r.benchmark_3yr_stdev),
        ])
    return out
