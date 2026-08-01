"""Brinson 성과 귀속 테스트.

가장 중요한 검증은 **항등식**이다: 배분+선택+상호작용 = 총 초과수익.
이게 어긋나면 어딘가 계산이 틀린 것이므로, 여러 구성에서 반복 확인한다.
"""
import pytest

from app.computation.portfolio.attribution import (
    brinson_attribution,
    format_attribution_rows,
)

SECTORS = ["Energy", "Financials", "Tech"]


def test_effects_sum_exactly_to_excess_return():
    """BHB 항등식 — 세 효과의 합이 총 초과수익과 일치해야 한다."""
    r = brinson_attribution(
        SECTORS,
        portfolio_weights=[0.5, 0.3, 0.2],
        benchmark_weights=[0.3, 0.3, 0.4],
        portfolio_returns=[0.10, 0.05, -0.02],
        benchmark_returns=[0.08, 0.05, 0.01],
    )
    total = r.total_allocation + r.total_selection + r.total_interaction
    assert total == pytest.approx(r.excess_return)


def test_identity_holds_when_portfolio_matches_benchmark():
    w, ret = [0.4, 0.35, 0.25], [0.05, -0.01, 0.08]
    r = brinson_attribution(SECTORS, w, w, ret, ret)
    assert r.excess_return == pytest.approx(0.0)
    assert r.total_allocation == pytest.approx(0.0)
    assert r.total_selection == pytest.approx(0.0)
    assert r.total_interaction == pytest.approx(0.0)


def test_pure_allocation_effect_when_returns_match_benchmark():
    """종목선택이 벤치마크와 동일하면 초과수익은 전부 배분효과여야 한다."""
    ret = [0.10, 0.02, -0.05]
    r = brinson_attribution(
        SECTORS,
        portfolio_weights=[0.6, 0.2, 0.2],
        benchmark_weights=[0.3, 0.3, 0.4],
        portfolio_returns=ret,
        benchmark_returns=ret,
    )
    assert r.total_selection == pytest.approx(0.0)
    assert r.total_interaction == pytest.approx(0.0)
    assert r.total_allocation == pytest.approx(r.excess_return)


def test_pure_selection_effect_when_weights_match_benchmark():
    """비중이 벤치마크와 같으면 초과수익은 전부 선택효과여야 한다."""
    w = [0.3, 0.3, 0.4]
    r = brinson_attribution(
        SECTORS,
        portfolio_weights=w,
        benchmark_weights=w,
        portfolio_returns=[0.12, 0.03, -0.01],
        benchmark_returns=[0.08, 0.05, 0.01],
    )
    assert r.total_allocation == pytest.approx(0.0)
    assert r.total_interaction == pytest.approx(0.0)
    assert r.total_selection == pytest.approx(r.excess_return)


def test_overweighting_an_outperforming_sector_gives_positive_allocation():
    """벤치마크 평균보다 좋았던 섹터를 더 담았으면 배분효과가 양(+)이어야 한다."""
    r = brinson_attribution(
        SECTORS,
        portfolio_weights=[0.6, 0.2, 0.2],
        benchmark_weights=[0.3, 0.3, 0.4],
        portfolio_returns=[0.10, 0.02, 0.01],
        benchmark_returns=[0.10, 0.02, 0.01],  # Energy가 벤치마크 평균보다 높다
    )
    energy = next(s for s in r.segments if s.name == "Energy")
    assert energy.active_weight > 0
    assert energy.allocation > 0


def test_segment_total_is_sum_of_three_effects():
    r = brinson_attribution(
        SECTORS,
        [0.5, 0.3, 0.2], [0.3, 0.3, 0.4],
        [0.10, 0.05, -0.02], [0.08, 0.05, 0.01],
    )
    for s in r.segments:
        assert s.total == pytest.approx(s.allocation + s.selection + s.interaction)


def test_portfolio_and_benchmark_returns_are_weighted_sums():
    r = brinson_attribution(
        SECTORS,
        [0.5, 0.3, 0.2], [0.3, 0.3, 0.4],
        [0.10, 0.05, -0.02], [0.08, 0.05, 0.01],
    )
    assert r.portfolio_return == pytest.approx(0.5 * 0.10 + 0.3 * 0.05 + 0.2 * -0.02)
    assert r.benchmark_return == pytest.approx(0.3 * 0.08 + 0.3 * 0.05 + 0.4 * 0.01)


# --- 입력 검증 ---

def test_rejects_weights_that_do_not_sum_to_one():
    """비중 합이 1이 아니면 초과수익 분해가 성립하지 않는다 — 조용히 틀린 값을
    내놓는 대신 거부해야 한다."""
    with pytest.raises(ValueError, match="비중 합"):
        brinson_attribution(
            SECTORS, [0.5, 0.3, 0.1], [0.3, 0.3, 0.4],
            [0.1, 0.05, 0.0], [0.08, 0.05, 0.01],
        )


def test_allows_tiny_rounding_error_in_weight_sum():
    brinson_attribution(
        SECTORS, [0.3333333, 0.3333333, 0.3333334], [0.3, 0.3, 0.4],
        [0.1, 0.05, 0.0], [0.08, 0.05, 0.01],
    )


def test_rejects_length_mismatch():
    with pytest.raises(ValueError, match="길이"):
        brinson_attribution(
            ["A", "B"], [0.5, 0.5], [0.5, 0.5], [0.1], [0.1, 0.2],
        )


def test_rejects_empty_segments():
    with pytest.raises(ValueError):
        brinson_attribution([], [], [], [], [])


# --- 표 출력 ---

def test_formatted_rows_include_total_line_matching_excess_return():
    r = brinson_attribution(
        SECTORS,
        [0.5, 0.3, 0.2], [0.3, 0.3, 0.4],
        [0.10, 0.05, -0.02], [0.08, 0.05, 0.01],
    )
    rows = format_attribution_rows(r)
    assert len(rows) == len(SECTORS) + 1
    assert rows[-1][0] == "합계"
    assert rows[-1][-1] == f"{r.excess_return * 100:+.2f}%"
