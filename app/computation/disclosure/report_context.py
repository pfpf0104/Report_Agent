"""필수 공시 페이지 + 데이터 계보 부록 — MASTER_PLAN Phase 4-2/4-3.

macro_regime 리포트에는 이미 DISCLOSURE 페이지(`macro_regime/report.html`)가
있지만, 그건 그 리포트가 다루는 3개 구성요소(headline 인용·상관행렬·레짐)에만
해당하는 문구다. CallRank·MetroGuard·밸류에이션은 각자 다른 데이터 출처와
방법론 한계를 갖고 있어 별도 문구가 필요하다 — 그래서 이 모듈은 리포트별로
분기하는 정적 콘텐츠를 반환한다(계산이 아니라 각 리포트에 이미 흩어져 있던
공시 문구를 한곳에 모아 공통 포맷으로 재배열한 것).

## 새로 문서화한 것 (기존 리포트 문구에 없던 내용)

- CallRank: `sector_embeddings.py`가 결정적 시드 기반 완전 합성 벡터라는
  구체적 사실(기존에는 "합성 데이터"로만 축약돼 있었다).
- MetroGuard: `city_ai_stub.py`가 실제 이력이 있으면 `global_rate_model.py`의
  PCA-Ridge 예측을 쓰고, 없으면 합성값으로 폴백한다는 조건부 로직.
- 밸류에이션: 시나리오 확률가중치(20/50/25/5%)가 과거 D램 사이클의 정성적
  참고일 뿐 통계적으로 추정된 값이 아니라는 점(4-5와 직결되는 갭).
- 공통: G5(point-in-time 강제 스키마는 있으나 일부 소스는 knowledge_date가
  조회일 근사치라는 한계) — 계보 표에서 각 소스별로 명시한다.
"""
from __future__ import annotations

_CONNECTOR_LABELS = {
    "fmp": "FMP (Financial Modeling Prep)",
    "yahoo_finance": "Yahoo Finance",
    "dart": "DART (전자공시시스템)",
    "kis": "KIS (한국투자증권 Open API)",
    "bok": "BOK (한국은행 경제통계시스템)",
    "fred": "FRED (세인트루이스 연은)",
}

_COMMON_DISCLAIMER = (
    "이 리포트는 투자 자문이나 매매 권유가 아니다. 여기 실린 수치는 방법론이 "
    "명시된 계산 결과이거나 출처가 명시된 실측치이며, 특정 종목의 매수·매도를 "
    "추천하지 않는다. 실제 투자 판단은 별도의 자문을 거쳐야 한다."
)


def _rows(*pairs: tuple[str, str]) -> list[list[str]]:
    return [[label, _CONNECTOR_LABELS.get(key, key)] for label, key in pairs]


_REPORT_CONTENT = {
    "callrank": {
        "methodology_limitations": [
            "섹터 랭킹의 입력인 실적발표 Q&A 임베딩은 실제 transcript가 아니라 "
            "기업코드 기반 결정적 시드로 생성한 합성 벡터다(`sector_embeddings.py`) "
            "— 실제 발언 내용을 반영하지 않는다.",
            "따라서 이번 버전의 랭킹 점수는 '기업이 실제로 무슨 말을 했는가'가 "
            "아니라 '고정된 임의 신호가 어떻게 정렬되는가'를 보여줄 뿐이다.",
            "성과 페이지가 백테스트하는 것은 섹터 ETF 리스크패리티 중립 배분이지 "
            "CallRank 랭킹 신호로 기울인 전략이 아니다 — 신호 자체가 합성인 "
            "동안은 신호 성과를 싣지 않는다.",
        ],
        "data_source_rows": _rows(
            ("섹터 ETF 종가", "yahoo_finance"),
            ("실적발표 Q&A 원문(현재는 미사용)", "fmp"),
        ),
        "conflict_of_interest": (
            "이 리포트를 생성하는 주체는 분석 대상 섹터 ETF·기업에 대해 어떠한 "
            "포지션도 사전에 공시하지 않는다. 별도 포지션 공시 체계는 아직 없다."
        ),
        "lineage_rows": [
            ["섹터 ETF MTD 수익률", "yahoo_finance_client.py", "fact_market_daily 실측 종가 차분"],
            ["섹터 랭킹 점수", "sector_embeddings.py + ridge_sector_rank.py", "합성 임베딩 → 리지 회귀 정렬(실측 아님)"],
            ["리스크패리티 중립 배분 성과", "backtest/engine.py", "워크포워드 비중 드리프트 + 거래비용 반영"],
        ],
    },
    "metroguard": {
        "methodology_limitations": [
            "목표 듀레이션(D*) 산출에 쓰이는 63거래일 금리변화 예측(q̂)은 "
            "`city_ai_stub.py`가 만든다 — 이력이 충분하면(36개월 이상) "
            "`global_rate_model.py`의 실제 PCA-8+Ridge λ=10 회귀 예측을 쓰지만, "
            "이력이 부족한 구간은 합성값으로 폴백한다.",
            "PCA-Ridge 모델의 입력은 한국(KTB1Y/KTB3Y)+미국 금리곡선 15개 "
            "지표로, 원본 설계의 49개 지표(미국 전국·도시 주택지표 9개 포함) "
            "중 17개만 실제로 소싱돼 있다(G4 잔여분).",
            "gate·경고 함수(carry-price gate, tanh 경고) 자체는 고정 수식이라 "
            "학습되지 않는다 — 변동성은 오직 입력 q̂에서만 온다.",
            "동결 성과는 비용 반영 지수 슬리브 시뮬레이션이며 사전등록된 "
            "실시간 운용 기록이 아니다.",
        ],
        "data_source_rows": _rows(
            ("국고채 1년물/3년물 금리", "bok"),
            ("미국 금리곡선(15개 만기)", "fred"),
            ("채권 ETF 종가(122260/114260)", "yahoo_finance"),
        ),
        "conflict_of_interest": (
            "이 리포트가 참조하는 채권 ETF·지수 슬리브에 대해 사전 포지션 "
            "공시 체계는 아직 없다."
        ),
        "lineage_rows": [
            ["국고채 1년물/3년물 금리(bp)", "ingest_macro_rates.py", "BOK 실측, %→bp 정규화"],
            ["63거래일 금리변화 예측 q̂", "city_ai_stub.py + global_rate_model.py", "이력 충분 시 PCA-Ridge 실측 예측, 부족 시 합성 폴백"],
            ["목표 듀레이션 D*", "duration_controller.py", "carry-price gate + 고정 tanh 경고 함수"],
        ],
    },
    "valuation": {
        "methodology_limitations": [
            "4개 시나리오(제한적/점진적/공격적 추격, 가격전쟁)의 확률가중치 "
            "(20/50/25/5%)는 과거 D램 사이클 국면에 대한 정성적 대응일 뿐, "
            "통계적으로 추정되거나 시장 데이터에서 역산된 값이 아니다.",
            "자기자본비용(9.5~12%)은 시나리오별로 분석가가 설정한 가정이며 "
            "CAPM 등으로 시장에서 역산한 값이 아니다.",
            "잔여이익모형(RIM)은 매 시점 독립적인 정적 평가라 시계열 백테스트나 "
            "GIPS 성과표 개념 자체가 적용되지 않는다 — 실제 주가 움직임을 "
            "예측하거나 검증하지 않는다.",
        ],
        "data_source_rows": _rows(
            ("주당순자산(BPS)", "dart"),
            ("현재가(실측 우선)", "kis"),
        ),
        "conflict_of_interest": (
            "이 리포트를 생성하는 주체는 삼성전자·SK하이닉스에 대해 어떠한 "
            "포지션도 사전에 공시하지 않는다."
        ),
        "lineage_rows": [
            ["장부가치(BPS)", "dart_client.py", "DART 최근 사업연도 공시, 실측 없으면 보고서 고정값"],
            ["현재가", "kis_client.py", "KIS 실측 우선, 실측 없으면 보고서 고정값"],
            ["확률가중 적정가", "residual_income_model.py", "시나리오별 ROE 경로 × 잔여이익 현재가치 + Gordon growth terminal value"],
        ],
    },
}


def build_disclosure_report_context(report_type: str) -> dict:
    content = _REPORT_CONTENT[report_type]
    return {
        "disclosure_available": True,
        "disclosure_methodology_limitations": content["methodology_limitations"],
        "disclosure_data_source_rows": content["data_source_rows"],
        "disclosure_conflict_of_interest": content["conflict_of_interest"],
        "disclosure_disclaimer": _COMMON_DISCLAIMER,
        "lineage_rows": content["lineage_rows"],
        "lineage_point_in_time_note": (
            "모든 시계열 사실(fact_market_daily·fact_financial_quarterly)은 "
            "knowledge_date 컬럼으로 point-in-time을 강제한다 — 조회 시점 "
            "이후에야 알려진 값은 그 시점 이전 리포트에 노출되지 않는다. "
            "단, 일부 소스는 실제 공표일이 아니라 근사치를 knowledge_date로 "
            "쓴다(예: 회계연도말+공시 지연 근사)."
        ),
    }
