"""제약 엔진 — 이론적 비중을 실제로 집행 가능한 비중으로 바꾼다.

리스크패리티가 한 자산에 40%를 배정해도 그대로 담을 수 있는 기관은 거의 없다.
종목·섹터 상한, 회전율 한도 같은 제약이 실제 운용 지침에 명시돼 있고,
국민연금식으로 말하면 "위험한도 내 집행"이 이 계층이다(references/README.md).

## 상한을 걸 때 흔한 오류 (1) — 클리핑 후 재정규화

비중을 상한으로 자른 뒤 **단순 재정규화하면 다시 상한을 넘는다.** 예를 들어
[0.6, 0.2, 0.15, 0.05]에 35% 상한을 걸면 [0.35, 0.2, 0.15, 0.05] → 합 0.75 →
정규화하면 [0.467, ...]로 첫 자산이 다시 위반이다. 그래서 이 모듈은 위반 자산을
상한에 **고정**하고 남은 예산만 나머지에 배분하는 것을 위반이 사라질 때까지
반복한다(최대 n회면 반드시 수렴한다 — 매 반복마다 최소 하나가 고정되므로).

## 상한을 걸 때 흔한 오류 (2) — 제약 간 상호 파괴

종목 상한을 맞춘 뒤 섹터 상한을 적용하면, 초과 섹터에서 빠진 예산이 다른 섹터로
흘러가 **종목 상한을 다시 깨뜨린다.** 실제로 [0.6, 0.2, 0.15, 0.05]에 종목 35% +
섹터 50%를 순차 적용하면 세 번째 자산이 0.375가 되어 종목 상한을 위반했다.
그래서 `apply_constraints`는 두 제약을 번갈아 적용해 둘 다 만족할 때까지 반복한다.

## 실현 가능성

두 상한이 각각 느슨해 보여도 함께 걸면 불가능할 수 있다. 달성 가능한 최대 합은
Σ_섹터 min(섹터상한, 섹터내_종목수 × 종목상한) 이며, 이것이 1.0 미만이면
어떤 비중도 제약을 만족하지 못한다 — 조용히 위반된 값을 내놓는 대신 예외를 던진다.

## 단위 규약

비중은 소수(0.30 = 30%), 합은 1.0. weighting.py·metrics.py와 동일하다.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

_TOLERANCE = 1e-9

# 종목/섹터 상한 교대 사영 최대 반복 횟수. 실현 가능하면 보통 2~3회에 수렴한다.
_MAX_PROJECTION_ROUNDS = 100


class InfeasibleConstraintError(ValueError):
    """제약을 동시에 만족하는 비중이 존재하지 않을 때."""


def _check_joint_feasibility(
    sectors: list[str], max_weight: float, max_sector_weight: float
) -> None:
    """종목 상한과 섹터 상한을 동시에 만족하는 비중이 존재하는지 확인한다.

    각 섹터가 담을 수 있는 최대치는 min(섹터상한, 섹터내_종목수 × 종목상한)이다.
    그 합이 1.0에 못 미치면 어떤 배분으로도 만족할 수 없다.
    """
    counts: dict[str, int] = {}
    for s in sectors:
        counts[s] = counts.get(s, 0) + 1

    achievable = sum(min(max_sector_weight, n * max_weight) for n in counts.values())
    if achievable < 1.0 - _TOLERANCE:
        raise InfeasibleConstraintError(
            f"종목 상한 {max_weight}과 섹터 상한 {max_sector_weight}를 함께 만족할 수 없다 "
            f"(달성 가능한 최대 합 {achievable:.4f} < 1.0)"
        )


def _normalize(w: np.ndarray) -> np.ndarray:
    total = w.sum()
    if total <= 0:
        return np.full(len(w), 1.0 / len(w))
    return w / total


def apply_weight_caps(weights, max_weight: float, min_weight: float = 0.0) -> np.ndarray:
    """종목별 상·하한을 만족하면서 합이 1.0이 되는 비중을 만든다.

    상한 위반 자산을 상한에 고정하고 남은 예산을 나머지에 비례 배분하는 것을
    반복한다 — 단순 클리핑 후 재정규화는 위반을 되살린다(모듈 docstring 참고).
    """
    w = np.asarray(list(weights), dtype=float)
    n = len(w)
    if n == 0:
        raise ValueError("비중이 비어 있다")
    if np.any(~np.isfinite(w)):
        raise ValueError("비중에 NaN/무한대가 있다")
    if min_weight > max_weight:
        raise InfeasibleConstraintError(f"하한({min_weight})이 상한({max_weight})보다 크다")
    if max_weight * n < 1.0 - _TOLERANCE:
        raise InfeasibleConstraintError(
            f"상한 {max_weight}로는 {n}개 자산의 합 1.0을 만들 수 없다(최대 {max_weight * n})"
        )
    if min_weight * n > 1.0 + _TOLERANCE:
        raise InfeasibleConstraintError(
            f"하한 {min_weight}로는 {n}개 자산의 합이 1.0을 넘는다(최소 {min_weight * n})"
        )

    w = _normalize(np.clip(w, 0.0, None))

    fixed = np.zeros(n, dtype=bool)
    for _ in range(n + 1):
        free = ~fixed
        if not free.any():
            break

        budget = 1.0 - float(w[fixed].sum())
        base = w[free]
        w[free] = (
            base * budget / base.sum() if base.sum() > _TOLERANCE else budget / free.sum()
        )

        over = free & (w > max_weight + _TOLERANCE)
        under = free & (w < min_weight - _TOLERANCE)
        if not over.any() and not under.any():
            break

        w[over] = max_weight
        w[under] = min_weight
        fixed |= over | under

    return w


def apply_sector_caps(weights, sectors: list[str], max_sector_weight: float) -> np.ndarray:
    """섹터 합계 상한. 초과 섹터는 상한까지 비례 축소하고, 남은 예산을 여유 섹터에 배분한다."""
    w = np.asarray(list(weights), dtype=float)
    if len(w) != len(sectors):
        raise ValueError(f"길이가 다르다: weights={len(w)}, sectors={len(sectors)}")

    unique = sorted(set(sectors))
    if max_sector_weight * len(unique) < 1.0 - _TOLERANCE:
        raise InfeasibleConstraintError(
            f"섹터 상한 {max_sector_weight}로는 {len(unique)}개 섹터의 합 1.0을 만들 수 없다"
        )

    w = _normalize(np.clip(w, 0.0, None))
    sector_array = np.array(sectors)

    capped_sectors: set[str] = set()
    for _ in range(len(unique) + 1):
        totals = {s: float(w[sector_array == s].sum()) for s in unique}
        violators = [s for s in unique if totals[s] > max_sector_weight + _TOLERANCE]
        if not violators:
            break

        for s in violators:
            mask = sector_array == s
            w[mask] *= max_sector_weight / totals[s]
            capped_sectors.add(s)

        free_mask = ~np.isin(sector_array, list(capped_sectors))
        budget = 1.0 - float(w[~free_mask].sum())
        free_sum = float(w[free_mask].sum())
        if not free_mask.any() or budget <= _TOLERANCE:
            break
        if free_sum > _TOLERANCE:
            w[free_mask] *= budget / free_sum
        else:
            w[free_mask] = budget / int(free_mask.sum())

    return w


def turnover(new_weights, current_weights) -> float:
    """단방향 회전율 = ½ Σ|Δw|.

    10%를 팔아 10%를 사면 회전율은 20%가 아니라 10%다 — 업계 표준 정의이며,
    ½을 빼먹으면 거래비용을 두 배로 계산하게 된다.
    """
    new = np.asarray(list(new_weights), dtype=float)
    cur = np.asarray(list(current_weights), dtype=float)
    if len(new) != len(cur):
        raise ValueError(f"길이가 다르다: new={len(new)}, current={len(cur)}")
    return float(np.abs(new - cur).sum() / 2.0)


def limit_turnover(target_weights, current_weights, max_turnover: float) -> np.ndarray:
    """회전율 한도를 넘으면 현재 비중 쪽으로 선형 보간해 정확히 한도에 맞춘다.

    회전율은 보간계수 λ에 선형이므로(w = w_cur + λ(w_tgt − w_cur)),
    λ = 한도/무제약회전율 로 정확히 한도에 도달한다 — 반복 탐색이 필요 없다.
    """
    target = np.asarray(list(target_weights), dtype=float)
    current = np.asarray(list(current_weights), dtype=float)
    if max_turnover < 0:
        raise ValueError("회전율 한도는 음수일 수 없다")

    raw = turnover(target, current)
    if raw <= max_turnover + _TOLERANCE or raw == 0:
        return target

    lam = max_turnover / raw
    return current + lam * (target - current)


@dataclass(frozen=True)
class ConstraintSet:
    """운용 지침을 코드로 옮긴 것. 전부 선택적이며, None이면 해당 제약을 걸지 않는다."""

    max_weight: float | None = None
    min_weight: float = 0.0
    max_sector_weight: float | None = None
    max_turnover: float | None = None


def apply_constraints(
    weights,
    constraints: ConstraintSet,
    sectors: list[str] | None = None,
    current_weights=None,
) -> np.ndarray:
    """제약을 순서대로 적용한다.

    순서: (종목 상·하한 ↔ 섹터 상한 교대 반복) → 회전율 한도.

    종목/섹터 상한을 **번갈아 반복**하는 이유: 한 번씩만 순차 적용하면 나중 것이
    먼저 것을 깨뜨린다(모듈 docstring "상호 파괴" 참고). 두 연산 모두 실현가능
    집합으로 수렴시키는 사영이라, 교대 적용하면 둘 다 만족하는 점에 도달한다.

    회전율을 **마지막에** 적용하는 이유: 회전율 제한은 현재 비중 쪽으로 되돌리는
    연산이라, 현재 비중이 이미 제약을 만족한다면 결과도 만족한다(볼록 결합).
    반대로 회전율을 먼저 적용하면 이후 상한 조정이 회전율을 다시 늘려 한도를 깨뜨린다.
    """
    w = np.asarray(list(weights), dtype=float)

    has_asset_cap = constraints.max_weight is not None or constraints.min_weight > 0
    has_sector_cap = constraints.max_sector_weight is not None
    asset_cap = constraints.max_weight if constraints.max_weight is not None else 1.0

    if has_sector_cap and sectors is None:
        raise ValueError("섹터 상한을 걸려면 sectors가 필요하다")

    if has_asset_cap and has_sector_cap:
        _check_joint_feasibility(sectors, asset_cap, constraints.max_sector_weight)

    for _ in range(_MAX_PROJECTION_ROUNDS):
        before = w.copy()
        if has_asset_cap:
            w = apply_weight_caps(w, asset_cap, constraints.min_weight)
        if has_sector_cap:
            w = apply_sector_caps(w, sectors, constraints.max_sector_weight)
        if np.max(np.abs(w - before)) < _TOLERANCE:
            break

    if constraints.max_turnover is not None:
        if current_weights is None:
            raise ValueError("회전율 한도를 걸려면 current_weights가 필요하다")
        w = limit_turnover(w, current_weights, constraints.max_turnover)

    return w
