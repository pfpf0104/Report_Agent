"""TODO(실데이터 연동): FMP earnings-call transcripts + SEC-BERT 임베딩으로 교체.

실제 파이프라인이 되어야 하는 모습:
  1. FMP API(키 필요)로 이번 분기·과거 분기 실적발표 Q&A transcript를 가져온다.
  2. 128/160/200단어 passage로 분할한다.
  3. nlpaueb/sec-bert-base로 각 passage를 임베딩한다(hidden size 768).
  4. 같은 기업의 과거 평균 임베딩을 빼 firm-conditioned signed residual을 만든다.

지금은 이 네 단계 전부를 결정적 시드 기반 합성 벡터로 대신한다 — 목적은
ridge_sector_rank.py의 알고리즘(고정 헤지 fit/score, 섹터 집계, 앙상블, 최소
섹터 게이트)이 실데이터가 들어와도 그대로 맞물려 동작하는지를, 의도적으로 심은
정답(leading_sector)을 알고리즘이 실제로 찾아내는지로 검증하기 위함이다.

기업 수는 첨부 CallRank 보고서의 "현재 섹터별 기업 수"(총 102개)를 그대로 썼다.
"""
from __future__ import annotations

import numpy as np

EMBEDDING_DIM = 768  # SEC-BERT-base hidden size

# 첨부 CallRank 보고서 5페이지 "현재 섹터별 기업 수"와 동일.
SECTOR_COMPANY_COUNTS = {
    "Financials": 35,
    "Industrials": 20,
    "Consumer Discretionary": 10,
    "Communication Services": 7,
    "Health Care": 7,
    "Information Technology": 7,
    "Consumer Staples": 5,
    "Materials": 4,
    "Energy": 4,
    "Utilities": 2,
    "Real Estate": 1,
}

SECTOR_ETF_BY_NAME = {
    "Financials": "XLF",
    "Industrials": "XLI",
    "Consumer Discretionary": "XLY",
    "Communication Services": "XLC",
    "Health Care": "XLV",
    "Information Technology": "XLK",
    "Consumer Staples": "XLP",
    "Materials": "XLB",
    "Energy": "XLE",
    "Utilities": "XLU",
    "Real Estate": "XLRE",
}


def _build_company_universe() -> dict[str, str]:
    sector_of: dict[str, str] = {}
    for sector, count in SECTOR_COMPANY_COUNTS.items():
        prefix = SECTOR_ETF_BY_NAME[sector]
        for i in range(count):
            sector_of[f"{prefix}_{i:02d}"] = sector
    return sector_of


SECTOR_OF_COMPANY = _build_company_universe()


def generate_current_residuals(
    seed: int, leading_sector: str, passage_length: int
) -> dict[str, np.ndarray]:
    """이번 달 기업별 firm-conditioned signed residual 합성 벡터.

    leading_sector에만 약한 양의 방향 신호를 심어서, 알고리즘이 그 섹터를
    실제로 1위로 찾아내는지(하드코딩이 아니라 계산으로) 검증할 수 있게 한다.
    passage_length를 시드에 섞어 128/160/200 세 모델이 서로 다르지만
    상관된 벡터를 갖도록 한다(같은 근본 신호를 다른 노이즈로 관측).
    """
    rng = np.random.default_rng(seed * 1000 + passage_length)
    vectors: dict[str, np.ndarray] = {}
    for code, sector in SECTOR_OF_COMPANY.items():
        vec = rng.normal(loc=0.0, scale=1.0, size=EMBEDDING_DIM)
        if sector == leading_sector:
            vec += rng.normal(loc=0.15, scale=0.05, size=EMBEDDING_DIM)
        vectors[code] = vec
    return vectors


def generate_frozen_hedge_training_set(
    seed: int, leading_sector: str, passage_length: int, n_periods: int = 24
) -> tuple[np.ndarray, np.ndarray]:
    """고정 헤지(표준화·PCA-32·Ridge)를 한 번만 학습할 pre-2021 합성 데이터셋.

    실제로는 FMP 과거 transcript 임베딩 + 과거 실현 성과로 대체된다. 라벨(y)은
    "이 회사가 leading_sector 소속인가"에 노이즈를 더해, Ridge가 회귀로
    그 방향을 실제로 학습하게 만든다(정답을 직접 리턴하는 게 아니라).
    """
    rng = np.random.default_rng(seed * 1000 + passage_length + 1)
    xs: list[np.ndarray] = []
    ys: list[float] = []
    for _ in range(n_periods):
        for code, sector in SECTOR_OF_COMPANY.items():
            vec = rng.normal(loc=0.0, scale=1.0, size=EMBEDDING_DIM)
            label = 1.0 if sector == leading_sector else 0.0
            if label:
                vec += rng.normal(loc=0.15, scale=0.05, size=EMBEDDING_DIM)
            xs.append(vec)
            ys.append(label + rng.normal(loc=0.0, scale=0.1))
    return np.array(xs), np.array(ys)
