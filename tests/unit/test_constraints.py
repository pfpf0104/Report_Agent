"""제약 엔진 테스트.

핵심 관심사는 "상한을 걸었는데 결과가 다시 상한을 넘지 않는가"다. 단순
클리핑 후 재정규화하면 위반이 되살아나므로, 여러 구성에서 반복 확인한다.
"""
import numpy as np
import pytest

from app.computation.portfolio.constraints import (
    ConstraintSet,
    InfeasibleConstraintError,
    apply_constraints,
    apply_sector_caps,
    apply_weight_caps,
    limit_turnover,
    turnover,
)

SECTORS = ["Tech", "Tech", "Energy", "Financials"]


# --- 종목 상한 ---

def test_cap_result_does_not_violate_after_renormalization():
    """단순 클리핑+정규화의 함정: [0.6,0.2,0.15,0.05]에 35% 상한을 걸고 정규화하면
    첫 자산이 0.467로 다시 위반한다. 반복 고정 방식은 이를 막아야 한다."""
    w = apply_weight_caps([0.6, 0.2, 0.15, 0.05], max_weight=0.35)
    assert w.sum() == pytest.approx(1.0)
    assert np.all(w <= 0.35 + 1e-9), f"상한 위반: {w}"


def test_cap_leaves_compliant_weights_unchanged():
    w = [0.4, 0.35, 0.25]
    assert apply_weight_caps(w, max_weight=0.50) == pytest.approx(w)


def test_cap_binding_on_all_assets_gives_equal_weight():
    """상한 × n == 1.0 이면 유일한 해는 전부 상한이다."""
    w = apply_weight_caps([0.7, 0.2, 0.1], max_weight=1 / 3)
    assert w == pytest.approx([1 / 3] * 3)


def test_cap_redistributes_excess_proportionally():
    # [0.6, 0.3, 0.1] 에 40% 상한 → 0.6이 0.4로, 남은 0.6을 3:1로 → [0.4, 0.45, 0.15]
    # 그런데 0.45가 다시 위반 → 재고정 → [0.4, 0.4, 0.2]
    w = apply_weight_caps([0.6, 0.3, 0.1], max_weight=0.40)
    assert np.all(w <= 0.40 + 1e-9)
    assert w.sum() == pytest.approx(1.0)
    assert w[2] > 0.1, "가장 작은 자산이 남은 예산을 흡수해야 한다"


def test_min_weight_floor_is_enforced():
    w = apply_weight_caps([0.9, 0.09, 0.01], max_weight=0.8, min_weight=0.05)
    assert np.all(w >= 0.05 - 1e-9)
    assert w.sum() == pytest.approx(1.0)


def test_negative_input_weights_are_clipped_to_long_only():
    w = apply_weight_caps([0.8, 0.4, -0.2], max_weight=0.6)
    assert np.all(w >= 0)
    assert w.sum() == pytest.approx(1.0)


def test_infeasible_when_cap_too_low_for_asset_count():
    with pytest.raises(InfeasibleConstraintError, match="상한"):
        apply_weight_caps([0.5, 0.5], max_weight=0.3)  # 0.3 × 2 = 0.6 < 1.0


def test_infeasible_when_floor_too_high():
    with pytest.raises(InfeasibleConstraintError, match="하한"):
        apply_weight_caps([0.5, 0.5], max_weight=1.0, min_weight=0.6)


def test_infeasible_when_floor_exceeds_cap():
    with pytest.raises(InfeasibleConstraintError):
        apply_weight_caps([0.5, 0.5], max_weight=0.3, min_weight=0.4)


# --- 섹터 상한 ---

def test_sector_cap_limits_group_total():
    # Tech 두 종목이 합쳐 0.7 → 50% 상한
    w = apply_sector_caps([0.4, 0.3, 0.2, 0.1], SECTORS, max_sector_weight=0.50)
    tech = w[0] + w[1]
    assert tech <= 0.50 + 1e-9, f"Tech 합계 위반: {tech}"
    assert w.sum() == pytest.approx(1.0)


def test_sector_cap_preserves_within_sector_ratio():
    """섹터를 축소해도 섹터 내부 상대 비율은 유지돼야 한다."""
    w = apply_sector_caps([0.4, 0.2, 0.2, 0.2], SECTORS, max_sector_weight=0.40)
    assert w[0] / w[1] == pytest.approx(2.0)


def test_sector_cap_leaves_compliant_unchanged():
    w = [0.2, 0.2, 0.3, 0.3]
    assert apply_sector_caps(w, SECTORS, max_sector_weight=0.60) == pytest.approx(w)


def test_sector_cap_infeasible_when_too_low():
    with pytest.raises(InfeasibleConstraintError):
        apply_sector_caps([0.25] * 4, SECTORS, max_sector_weight=0.20)  # 3섹터 × 0.2 < 1.0


def test_joint_caps_infeasible_is_rejected():
    """종목·섹터 상한이 각각은 느슨해 보여도 함께 걸면 불가능할 수 있다.
    Tech 2종목 × 20% = 40%지만 Energy/Financials는 1종목뿐이라 각 20%가 한계 →
    최대 40+20+20 = 80% < 100%."""
    with pytest.raises(InfeasibleConstraintError, match="함께 만족"):
        apply_constraints(
            [0.25] * 4,
            ConstraintSet(max_weight=0.20, max_sector_weight=0.90),
            sectors=SECTORS,
        )


def test_sector_cap_rejects_length_mismatch():
    with pytest.raises(ValueError):
        apply_sector_caps([0.5, 0.5], SECTORS, max_sector_weight=0.5)


# --- 회전율 ---

def test_turnover_is_one_way_not_double_counted():
    """10%를 팔아 10%를 사면 회전율은 10%다. ½을 빼먹으면 비용이 두 배가 된다."""
    assert turnover([0.6, 0.4], [0.5, 0.5]) == pytest.approx(0.10)


def test_turnover_zero_when_unchanged():
    w = [0.3, 0.3, 0.4]
    assert turnover(w, w) == 0.0


def test_limit_turnover_hits_the_cap_exactly():
    target, current = [0.8, 0.2], [0.2, 0.8]
    limited = limit_turnover(target, current, max_turnover=0.20)
    assert turnover(limited, current) == pytest.approx(0.20)


def test_limit_turnover_leaves_small_trades_alone():
    target, current = [0.55, 0.45], [0.5, 0.5]
    assert limit_turnover(target, current, max_turnover=0.20) == pytest.approx(target)


def test_limit_turnover_preserves_direction():
    """한도를 걸어도 목표 방향으로는 움직여야 한다."""
    target, current = [0.9, 0.1], [0.5, 0.5]
    limited = limit_turnover(target, current, max_turnover=0.10)
    assert limited[0] > current[0]
    assert limited[0] < target[0]


def test_limit_turnover_result_still_sums_to_one():
    limited = limit_turnover([0.8, 0.2], [0.2, 0.8], max_turnover=0.15)
    assert limited.sum() == pytest.approx(1.0)


def test_limit_turnover_rejects_negative_limit():
    with pytest.raises(ValueError):
        limit_turnover([0.5, 0.5], [0.5, 0.5], max_turnover=-0.1)


# --- 통합 ---

def test_apply_constraints_satisfies_all_simultaneously():
    current = [0.25, 0.25, 0.25, 0.25]
    result = apply_constraints(
        [0.6, 0.2, 0.15, 0.05],
        ConstraintSet(max_weight=0.35, max_sector_weight=0.50, max_turnover=0.30),
        sectors=SECTORS,
        current_weights=current,
    )
    assert result.sum() == pytest.approx(1.0)
    assert np.all(result <= 0.35 + 1e-9), "종목 상한 위반"
    assert result[0] + result[1] <= 0.50 + 1e-9, "섹터 상한 위반"
    assert turnover(result, current) <= 0.30 + 1e-9, "회전율 위반"


def test_apply_constraints_with_no_constraints_is_identity():
    w = [0.4, 0.3, 0.2, 0.1]
    assert apply_constraints(w, ConstraintSet()) == pytest.approx(w)


def test_sector_cap_requires_sectors():
    with pytest.raises(ValueError, match="sectors"):
        apply_constraints([0.5, 0.5], ConstraintSet(max_sector_weight=0.6))


def test_turnover_limit_requires_current_weights():
    with pytest.raises(ValueError, match="current_weights"):
        apply_constraints([0.5, 0.5], ConstraintSet(max_turnover=0.1))
