"""backfill_macro_rates.py의 연도 범위 계산 회귀 테스트.

실제로 발견된 버그: range(year - N + 1, year + 1)로 계산하면 5년 전(온전한
연도)이 빠지고 대신 아직 다 지나지 않은 올해가 들어가, 결과적으로 5년에
못 미치는 기간만 백필됐다. GIPS는 최소 5년 연간 수익률을 요구하므로 이 계산이
틀리면 조용히 요건 미달 상태가 된다(예외 없이 그냥 데이터가 적게 쌓일 뿐).
"""
from datetime import date

from app.ingestion.jobs.backfill_macro_rates import _backfill_year_range


def test_year_range_includes_five_full_years_plus_current():
    years = _backfill_year_range(date(2026, 8, 1), backfill_years=5)
    assert years == [2021, 2022, 2023, 2024, 2025, 2026]


def test_year_range_covers_at_least_five_full_prior_years():
    """핵심 회귀: 오늘로부터 5년 전 1월 1일이 범위에 포함돼야 한다 —
    이게 없으면 GIPS 5년 요건을 못 채운다."""
    today = date(2026, 8, 1)
    years = _backfill_year_range(today, backfill_years=5)
    five_years_ago = today.year - 5
    assert five_years_ago in years


def test_year_range_respects_custom_backfill_years():
    years = _backfill_year_range(date(2026, 1, 1), backfill_years=1)
    assert years == [2025, 2026]
