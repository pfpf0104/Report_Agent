"""CallRank: 고정 헤지(표준화·PCA-32·Ridge) 기반 섹터 랭킹 + 리포트 context 빌더.

방법론 요약 (첨부 CallRank 보고서 2~5페이지 기준):
  1. 기업의 이번 분기 실적발표 Q&A를 128/160/200단어 passage로 나눠 임베딩한다.
  2. 같은 기업의 과거 평균 임베딩을 빼 firm-conditioned signed residual을 만든다
     (기업마다 원래 말투가 다르므로 자기 자신을 기준선으로 삼는다).
  3. 기업별 residual을 섹터 안에서 동일 가중으로 평균한다(기업 먼저, 섹터 나중 —
     시가총액이 신호를 지배하지 않도록).
  4. pre-2021 데이터로 미리 고정한 표준화·PCA-32·Ridge 계수를 이후 매월 그대로
     적용한다(재학습 없음 — "고정 헤지").
  5. 128/160/200단어 세 모델의 섹터 순위(정규화 점수)를 평균해 최종 순위를 낸다.
  6. 최소 6개 섹터가 있어야 거래 결정을 연다.

입력 임베딩은 sector_embeddings.py의 합성 데이터다 — 그 파일의 TODO 참고.
이 파일의 알고리즘(고정 헤지 fit/score, 섹터 집계, 앙상블, 최소 섹터 게이트)은
순수 함수라 실제 SEC-BERT 임베딩으로 교체해도 그대로 재사용된다.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import numpy as np
from sklearn.decomposition import PCA
from sklearn.linear_model import Ridge
from sklearn.preprocessing import StandardScaler
from sqlalchemy.orm import Session

from app.computation.quant.sector_embeddings import (
    SECTOR_ETF_BY_NAME,
    SECTOR_OF_COMPANY,
    generate_current_residuals,
    generate_frozen_hedge_training_set,
)
from app.db.models.dim_asset import DimAsset
from app.db.models.fact_market_daily import FactMarketDaily

PASSAGE_LENGTHS = (128, 160, 200)
MIN_ELIGIBLE_SECTORS = 6


@dataclass(frozen=True)
class FrozenHedge:
    """pre-2021 데이터로 한 번 fit하고 이후 재학습 없이 그대로 쓰는 계수 묶음."""

    scaler_mean: np.ndarray
    scaler_scale: np.ndarray
    pca_components: np.ndarray  # (n_components, n_features)
    pca_mean: np.ndarray
    ridge_coef: np.ndarray  # (n_components,)
    ridge_intercept: float


def fit_frozen_hedge(x_hist: np.ndarray, y_hist: np.ndarray, n_components: int = 32) -> FrozenHedge:
    """과거(pre-2021) 임베딩·타깃으로 표준화·PCA·Ridge를 한 번만 학습한다."""
    scaler = StandardScaler().fit(x_hist)
    x_scaled = scaler.transform(x_hist)

    n_components = min(n_components, x_scaled.shape[0], x_scaled.shape[1])
    pca = PCA(n_components=n_components).fit(x_scaled)
    x_pca = pca.transform(x_scaled)

    ridge = Ridge(alpha=1.0).fit(x_pca, y_hist)

    return FrozenHedge(
        scaler_mean=scaler.mean_,
        scaler_scale=scaler.scale_,
        pca_components=pca.components_,
        pca_mean=pca.mean_,
        ridge_coef=ridge.coef_,
        ridge_intercept=float(ridge.intercept_),
    )


def score_normalized_direction(x: np.ndarray, hedge: FrozenHedge) -> np.ndarray:
    """고정된 계수로만 transform·predict한다 — 재학습 없음(walk-forward 고정 헤지)."""
    x_scaled = (x - hedge.scaler_mean) / hedge.scaler_scale
    x_centered = x_scaled - hedge.pca_mean
    x_pca = x_centered @ hedge.pca_components.T
    return x_pca @ hedge.ridge_coef + hedge.ridge_intercept


def score_raw_residual(residual_vectors: np.ndarray) -> np.ndarray:
    """PCA·Ridge를 거치지 않는 원시 신호. signed L2 norm으로 방향+크기를 보존한다.

    실제 raw residual 산식은 임베딩 공간의 특정 대조 방향을 쓸 수 있으나,
    여기서는 부호(평균 성분의 부호)와 크기(L2 norm)를 함께 보존하는 근사치를 쓴다.
    """
    sign = np.sign(residual_vectors.mean(axis=1))
    sign[sign == 0] = 1.0
    return sign * np.linalg.norm(residual_vectors, axis=1)


def aggregate_company_to_sector(
    company_scores: dict[str, float], sector_of: dict[str, str]
) -> dict[str, float]:
    """기업 먼저, 섹터 나중 — 섹터 내 기업은 동일 가중 평균(시가총액이 신호를 지배하지 않음)."""
    buckets: dict[str, list[float]] = {}
    for code, score in company_scores.items():
        sector = sector_of.get(code)
        if sector is None:
            continue
        buckets.setdefault(sector, []).append(score)
    return {sector: float(np.mean(scores)) for sector, scores in buckets.items()}


def rank_sectors(
    sector_scores: dict[str, float], min_sectors: int = MIN_ELIGIBLE_SECTORS
) -> list[dict] | None:
    """섹터 점수를 정렬하고 최고점 대비 비율로 정규화한다. 최소 섹터 수 미달이면 None."""
    if len(sector_scores) < min_sectors:
        return None

    ranked = sorted(sector_scores.items(), key=lambda kv: kv[1], reverse=True)
    top_score = ranked[0][1]
    denom = top_score if top_score > 0 else 1.0

    return [
        {"sector": sector, "raw_score": score, "normalized_score": round(score / denom, 3)}
        for sector, score in ranked
    ]


def ensemble_rank(per_length_rankings: list[list[dict]]) -> list[dict]:
    """128/160/200단어 세 모델의 정규화 점수를 평균해 최종 순위를 만든다."""
    accum: dict[str, list[float]] = {}
    for ranking in per_length_rankings:
        for row in ranking:
            accum.setdefault(row["sector"], []).append(row["normalized_score"])

    averaged = {sector: float(np.mean(scores)) for sector, scores in accum.items()}
    ranked = sorted(averaged.items(), key=lambda kv: kv[1], reverse=True)
    return [{"sector": sector, "score": round(score, 3)} for sector, score in ranked]


def run_sector_ranking(as_of: date, leading_sector_seed: str = "Energy") -> dict:
    """세 passage 길이 모델을 각각 고정 헤지로 학습·적용하고 앙상블 순위를 낸다.

    leading_sector_seed는 합성 데이터에 심는 신호일 뿐, 실제로 그 섹터가
    1위로 나오는지는 아래 계산이 실제로 판단한다(하드코딩된 결과가 아니다).
    """
    seed = as_of.toordinal()

    normalized_rankings: list[list[dict]] = []
    raw_rankings: list[list[dict]] = []

    for passage_length in PASSAGE_LENGTHS:
        x_hist, y_hist = generate_frozen_hedge_training_set(seed, leading_sector_seed, passage_length)
        hedge = fit_frozen_hedge(x_hist, y_hist)

        current = generate_current_residuals(seed, leading_sector_seed, passage_length)
        codes = list(current.keys())
        x_current = np.array([current[c] for c in codes])

        normalized_scores = score_normalized_direction(x_current, hedge)
        raw_scores = score_raw_residual(x_current)

        company_normalized = dict(zip(codes, normalized_scores))
        company_raw = dict(zip(codes, raw_scores))

        sector_normalized = aggregate_company_to_sector(company_normalized, SECTOR_OF_COMPANY)
        sector_raw = aggregate_company_to_sector(company_raw, SECTOR_OF_COMPANY)

        ranked_normalized = rank_sectors(sector_normalized)
        ranked_raw = rank_sectors(sector_raw)

        if ranked_normalized is None or ranked_raw is None:
            continue
        normalized_rankings.append(ranked_normalized)
        raw_rankings.append(ranked_raw)

    if len(normalized_rankings) < len(PASSAGE_LENGTHS):
        return {"eligible": False, "reason": f"최소 {MIN_ELIGIBLE_SECTORS}개 섹터 미충족"}

    return {
        "eligible": True,
        "normalized_direction": ensemble_rank(normalized_rankings),
        "raw_residual": ensemble_rank(raw_rankings),
        "model_agreement": {
            str(length): ranking[0]["sector"]
            for length, ranking in zip(PASSAGE_LENGTHS, normalized_rankings)
        },
    }


def _tone(value: float | None) -> str | None:
    if value is None:
        return None
    if value > 0:
        return "up"
    if value < 0:
        return "down"
    return None


def _mtd_return(db: Session, asset_id: int, as_of: date) -> float | None:
    month_start = as_of.replace(day=1)

    start_row = (
        db.query(FactMarketDaily)
        .filter(FactMarketDaily.asset_id == asset_id, FactMarketDaily.trade_date < month_start)
        .order_by(FactMarketDaily.trade_date.desc())
        .first()
    )
    end_row = (
        db.query(FactMarketDaily)
        .filter(FactMarketDaily.asset_id == asset_id, FactMarketDaily.trade_date <= as_of)
        .order_by(FactMarketDaily.trade_date.desc())
        .first()
    )
    if not start_row or not end_row or not start_row.adj_close:
        return None
    return float(end_row.adj_close / start_row.adj_close - 1) * 100


def build_callrank_context(db: Session, as_of: date, leading_sector_seed: str = "Energy") -> dict:
    ranking = run_sector_ranking(as_of, leading_sector_seed)

    if not ranking["eligible"]:
        return {
            "as_of": as_of.isoformat(),
            "generated_at": as_of.isoformat(),
            "cards": [{"label": "섹터 랭킹", "value": "판단 보류", "caption": ranking["reason"], "tone": None}],
            "ranking_rows": [],
            "cross_check_rows": [],
            "model_agreement": {},
            "what_and_why_cards": WHAT_AND_WHY_CARDS,
            "workflow_steps": PROCESS_STEPS,
            "backtest_chart_uri": _build_backtest_chart(as_of),
            "backtest_summary": BACKTEST_SUMMARY,
        }

    top_sector = ranking["normalized_direction"][0]["sector"]
    top_etf_code = SECTOR_ETF_BY_NAME[top_sector]

    asset = db.query(DimAsset).filter_by(code=top_etf_code).first()
    mtd_return = _mtd_return(db, asset.asset_id, as_of) if asset else None

    cards = [
        {
            "label": f"{as_of.month}월 잠정 1위 섹터",
            "value": f"{top_sector}({top_etf_code})",
            "caption": "정규화 방향·Raw residual 앙상블 평균 기준",
            "tone": "up",
        },
        {
            "label": f"{as_of.month}월 MTD {top_etf_code}",
            "value": f"{mtd_return:+.2f}%" if mtd_return is not None else "데이터 없음",
            "caption": "fact_market_daily 실측 (시드 데이터가 있는 자산만 표시)",
            "tone": _tone(mtd_return),
        },
    ]

    ranking_rows = [
        [str(i + 1), row["sector"], f"{row['score']:.3f}"]
        for i, row in enumerate(ranking["normalized_direction"][:5])
    ]
    cross_check_rows = [
        [str(i + 1), n["sector"], f"{n['score']:.3f}", r["sector"], f"{r['score']:.3f}"]
        for i, (n, r) in enumerate(
            zip(ranking["normalized_direction"][:5], ranking["raw_residual"][:5])
        )
    ]

    return {
        "as_of": as_of.isoformat(),
        "generated_at": as_of.isoformat(),
        "cards": cards,
        "ranking_rows": ranking_rows,
        "cross_check_rows": cross_check_rows,
        "model_agreement": ranking["model_agreement"],
        "what_and_why_cards": WHAT_AND_WHY_CARDS,
        "workflow_steps": PROCESS_STEPS,
        "backtest_chart_uri": _build_backtest_chart(as_of),
        "backtest_summary": BACKTEST_SUMMARY,
    }


# 방법론 설명(월별로 바뀌지 않는 고정 콘텐츠) — 첨부 CallRank 보고서 2페이지 기준.
WHAT_AND_WHY_CARDS = [
    {
        "title": "1. 기업마다 원래 말투가 다르다",
        "body": "제품명, 경영진 이름, 회계용어와 반복 설명이 매 분기 들어온다. 다른 기업과 바로 비교하면 이런 고정된 정체성이 신호처럼 보일 수 있어, 자기 과거를 기준선으로 삼는다.",
    },
    {
        "title": '2. "얼마나"보다 "어느 쪽으로"가 중요하다',
        "body": "두 기업이 과거에서 같은 거리만큼 달라져도 요구·비용·투자·규제에 관한 방향은 반대일 수 있다. CallRank는 변화량의 부호와 방향을 보존한다.",
    },
    {
        "title": "3. 큰 기업 한 곳이 섹터를 지배하지 않는다",
        "body": "운용형에서는 기업별 변화 벡터를 동일 가중으로 바꿔 한 기업당 한 표를 준다. transcript 길이나 변화 크기보다 여러 기업이 가리키는 공통 방향을 본다.",
    },
]

# 월말 워크포워드 절차(고정 6단계) — 첨부 보고서 3페이지 기준.
PROCESS_STEPS = [
    {"title": "콜 원장 확인", "body": "기업 identity, 실제 콜 월, 당시 S&P 500 편입과 섹터를 확인한다."},
    {"title": "세 길이로 읽기", "body": "Q&A를 128·160·200단어 passage로 나눠 고정 SEC-BERT로 임베딩한다."},
    {"title": "기업의 정상상태 제거", "body": "현재 벡터에서 같은 기업의 이전 Q&A 평균을 빼 부호 있는 잔차를 만든다."},
    {"title": "기업 먼저, 섹터 나중", "body": "기업별 한 표를 동일 가중한 뒤 섹터 벡터를 만든다. 시가총액이 신호를 지배하지 않는다."},
    {"title": "고정 헤지로 순위화", "body": "pre-2021 자료로 정한 표준화·PCA-32·Ridge 계수를 이후 매월 그대로 적용한다."},
    {"title": "세 모델의 순위 평균", "body": "세 passage 길이의 섹터 순위를 평균하고, 최소 6개 섹터가 있어야 거래 결정을 연다."},
]

BACKTEST_SUMMARY = [
    {"label": "Top 1 연환산 순수익률", "value": "42.1%", "caption": "연구 백테스트(합성 예시)"},
    {"label": "같은 기간 SPY(S&P 500)", "value": "24.0%", "caption": "연구 백테스트(합성 예시)"},
    {"label": "Treasury 조정 Sharpe", "value": "2.05", "caption": "연구 백테스트(합성 예시)"},
]


def _build_backtest_chart(as_of: date) -> str:
    """연구 백테스트 누적 성과 예시 차트. 실제 이력 데이터가 없어 결정적 시드로
    그럴듯한 곡선을 만든다 — 첨부 보고서 4페이지의 형태(Top1 > SPY > Bottom1,
    셋 다 100에서 시작해 우상향)만 재현하는 자리표시 차트다.
    """
    from app.rendering.chart_service import line_chart

    rng = np.random.default_rng(as_of.toordinal())
    n = 24
    x_labels = [f"{2024 + i // 12}-{(i % 12) + 1:02d}" for i in range(n)]

    def _walk(drift: float, vol: float) -> list[float]:
        steps = rng.normal(drift, vol, size=n)
        return list(100 * np.cumprod(1 + steps))

    series = {
        "Top 1": _walk(0.028, 0.05),
        "SPY(S&P 500)": _walk(0.017, 0.035),
        "Bottom 1": _walk(0.006, 0.05),
    }
    return line_chart(x_labels, series, figsize=(6.2, 2.4))
