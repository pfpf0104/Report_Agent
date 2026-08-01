"""비중 산출 테스트.

리스크패리티는 "돌아간다"로는 검증이 안 된다. 해석적으로 답을 아는 케이스
(무상관 → 역변동성, 동일자산 → 균등)와 정의 자체(위험기여도가 실제로 같은가)로
확인한다.
"""
import numpy as np
import pytest

from app.computation.portfolio.weighting import (
    apply_scores_as_tilt,
    equal_weight,
    inverse_volatility,
    portfolio_volatility,
    risk_contributions,
    risk_parity,
)


def _diagonal_cov(vols) -> np.ndarray:
    """무상관 공분산 — 대각만 채운다."""
    return np.diag(np.asarray(vols, dtype=float) ** 2)


def _cov_from(vols, corr) -> np.ndarray:
    v = np.asarray(vols, dtype=float)
    c = np.asarray(corr, dtype=float)
    return np.outer(v, v) * c


# --- 동일가중 ---

def test_equal_weight_sums_to_one():
    w = equal_weight(4)
    assert w == pytest.approx([0.25] * 4)
    assert w.sum() == pytest.approx(1.0)


def test_equal_weight_rejects_non_positive():
    with pytest.raises(ValueError):
        equal_weight(0)


# --- 역변동성 ---

def test_inverse_volatility_favors_low_vol_asset():
    # σ = [10%, 20%] → w ∝ [1/0.1, 1/0.2] = [10, 5] → [2/3, 1/3]
    w = inverse_volatility([0.10, 0.20])
    assert w == pytest.approx([2 / 3, 1 / 3])


def test_inverse_volatility_rejects_zero_vol():
    """0 변동성은 비중을 발산시킨다 — 조용히 inf를 만들지 않고 거부해야 한다."""
    with pytest.raises(ValueError):
        inverse_volatility([0.10, 0.0])


# --- 위험 기여도 ---

def test_risk_contributions_sum_to_portfolio_volatility():
    """오일러 분해의 핵심 성질 — 이게 성립해야 '전체 위험의 몇 %'를 말할 수 있다."""
    cov = _cov_from([0.15, 0.25], [[1.0, 0.3], [0.3, 1.0]])
    w = np.array([0.6, 0.4])
    assert risk_contributions(w, cov).sum() == pytest.approx(portfolio_volatility(w, cov))


def test_risk_contributions_zero_when_no_risk():
    assert risk_contributions([0.5, 0.5], np.zeros((2, 2))) == pytest.approx([0.0, 0.0])


# --- 리스크패리티 (ERC) ---

def test_risk_parity_equalizes_risk_contributions():
    """정의 그 자체 — 수렴점에서 모든 자산의 위험기여도가 같아야 한다."""
    cov = _cov_from([0.10, 0.20, 0.35], [[1.0, 0.2, 0.4], [0.2, 1.0, 0.1], [0.4, 0.1, 1.0]])
    w = risk_parity(cov)

    rc = risk_contributions(w, cov)
    assert rc.max() - rc.min() < 1e-8, f"위험기여도가 균등하지 않다: {rc}"
    assert w.sum() == pytest.approx(1.0)
    assert np.all(w > 0), "롱온리여야 한다"


def test_risk_parity_reduces_to_inverse_volatility_when_uncorrelated():
    """무상관이면 ERC의 해석적 해가 역변동성과 정확히 일치한다."""
    vols = [0.10, 0.20, 0.40]
    w = risk_parity(_diagonal_cov(vols))
    assert w == pytest.approx(inverse_volatility(vols), abs=1e-9)


def test_risk_parity_is_equal_weight_for_identical_assets():
    cov = _cov_from([0.2, 0.2, 0.2], [[1.0, 0.5, 0.5], [0.5, 1.0, 0.5], [0.5, 0.5, 1.0]])
    assert risk_parity(cov) == pytest.approx([1 / 3] * 3, abs=1e-9)


def test_risk_parity_underweights_the_riskiest_asset():
    """동일가중과 달리 위험이 큰 자산을 실제로 덜 담아야 한다."""
    cov = _cov_from([0.05, 0.30], [[1.0, 0.0], [0.0, 1.0]])
    w = risk_parity(cov)
    assert w[0] > w[1]


def test_risk_parity_single_asset():
    assert risk_parity(np.array([[0.04]])) == pytest.approx([1.0])


def test_risk_parity_rejects_non_square():
    with pytest.raises(ValueError):
        risk_parity(np.array([[1.0, 0.0]]))


def test_risk_parity_rejects_non_positive_variance():
    with pytest.raises(ValueError):
        risk_parity(np.array([[0.0, 0.0], [0.0, 0.04]]))


def test_risk_parity_falls_back_gracefully_on_degenerate_covariance():
    """완전상관(특이행렬)이면 한계기여도가 불안정해질 수 있다 — 예외로 죽지 않고
    역변동성으로 후퇴해야 한다."""
    cov = _cov_from([0.2, 0.2], [[1.0, 1.0], [1.0, 1.0]])
    w = risk_parity(cov)
    assert w.sum() == pytest.approx(1.0)
    assert np.all(np.isfinite(w))


# --- 신호 기울이기 ---

def test_tilt_zero_strength_returns_base_unchanged():
    base = [0.5, 0.3, 0.2]
    assert apply_scores_as_tilt(base, [1.0, 5.0, 3.0], tilt_strength=0.0) == pytest.approx(base)


def test_tilt_moves_weight_toward_higher_score():
    base = equal_weight(3)
    w = apply_scores_as_tilt(base, [0.0, 0.0, 1.0], tilt_strength=0.5)
    assert w[2] > base[2]
    assert w.sum() == pytest.approx(1.0)


def test_tilt_with_identical_scores_keeps_base():
    """점수가 전부 같으면 기울일 방향이 없다 — 0으로 나누지 않고 균등으로 처리."""
    base = equal_weight(3)
    assert apply_scores_as_tilt(base, [2.0, 2.0, 2.0], tilt_strength=1.0) == pytest.approx(base)


def test_tilt_rejects_invalid_strength():
    with pytest.raises(ValueError):
        apply_scores_as_tilt([0.5, 0.5], [1.0, 2.0], tilt_strength=1.5)


def test_tilt_rejects_length_mismatch():
    with pytest.raises(ValueError):
        apply_scores_as_tilt([0.5, 0.5], [1.0])


def test_tilt_output_is_long_only_and_normalized():
    w = apply_scores_as_tilt(equal_weight(4), [-5.0, 0.0, 3.0, 10.0], tilt_strength=1.0)
    assert np.all(w >= 0)
    assert w.sum() == pytest.approx(1.0)
