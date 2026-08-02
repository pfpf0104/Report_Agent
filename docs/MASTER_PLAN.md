# Report Agent — Master Plan

기관투자자(연기금·Bridgewater·Fidelity) 수준의 리서치 리포트를 자동 생성하는 것을
목표로 한 개발 로드맵. 2026-08 기준 코드 리뷰 결과와 기관 리포트 표준 조사를 근거로
작성했다.

---

## 0. 현재 상태 진단 (냉정한 평가)

### 0.1 잘 만들어진 것

| 영역 | 평가 |
|---|---|
| 아키텍처 계층 분리 | ingestion / computation / rendering / sync가 깔끔하게 분리돼 있고 순환의존 없음 |
| 디자인 SSOT | `design_tokens.py` 하나로 CSS와 matplotlib 색상이 일치. 한국식 관례(적=상승) 준수 |
| 알고리즘 정확성 | RIM 적정가를 원 단위로, tanh 경고를 소수점 3자리로 원본 리포트 예시와 대조 검산 |
| 시크릿 위생 | URL 경로/쿼리 마스킹, `.env.local` 분리, `extra_secrets` 전파. 커밋 전 grep 습관 |
| 실측 우선 + 폴백 투명성 | 현재가(KIS)·BPS(DART) 모두 "실측 있으면 실측, 없으면 폴백 + 출처 명시" 패턴 |
| 테스트 | 88개. 핵심 알고리즘 회귀 테스트 + respx 모킹 + 실제 Postgres 통합 테스트 |

이 기반은 버릴 게 없다. 아래 계획은 전부 이 위에 쌓는다.

### 0.2 치명적 갭 — **리포트는 껍데기다**

가장 중요한 발견부터. 로컬 DB 실측 결과:

```
dim_asset                    0
fact_market_daily            0
fact_financial_quarterly     0
fact_real_estate_deal        0
ingestion_run                0
```

**모든 테이블이 비어 있다.** 즉 지금 렌더링되는 3종 리포트 24페이지는 전부
합성 데이터·하드코딩 상수로 만들어진 것이다. 시각적 완성도는 원본 수준에
도달했지만, **숫자는 아직 하나도 진짜가 아니다.**

Bridgewater 수준과의 격차는 "페이지가 부족해서"도 "차트가 못생겨서"도 아니다.
**숫자가 진짜가 아니라서**다. 이 문서의 우선순위는 전부 여기서 출발한다.

구체적 목록:

| # | 갭 | 위치 | 심각도 |
|---|---|---|---|
| G1 | DB 전체가 비어 있음 — 인제스천 잡이 프로덕션에서 한 번도 안 돌았음 | 전역 | **치명** |
| G2 | 백테스트 성과가 하드코딩 문자열 ("42.1%", "Sharpe 2.05") | `ridge_sector_rank.py:306` | **치명** |
| G3 | CallRank 신호가 순수 난수 — 실제 transcript 임베딩 없음 | `sector_embeddings.py` | **치명** |
| G4 | MetroGuard City AI가 스텁 — 49개 입력 PCA-Ridge 미구현 | `city_ai_stub.py` | 높음 |
| G5 | Point-in-time 정합성 장치 없음. 리포트 본문은 "7일 embargo"를 주장하지만 스키마에 공시시점 컬럼이 없어 강제 불가 | `db/models/*` | 높음 |
| G6 | ~~리스크 지표 전무~~ — **해소**(Phase 1 완료). 팩터 노출은 미구현 | `risk/metrics.py` | ~~높음~~ |
| G7 | ~~포트폴리오 구성 없음~~ — **해소**(Phase 2 완료) | `portfolio/` | ~~높음~~ |
| G8 | ~~성과 귀속 없음~~ — **해소** Brinson 3요소 분해 구현 | `portfolio/attribution.py` | ~~중간~~ |
| G9 | 3개 리포트가 서로 대화하지 않음 (크로스에셋 뷰 부재) | 전역 | 중간 |
| G10 | 컴플라이언스·면책 프레임 없음 | 없음 | 중간 |
| G11 | 부동산 인제스천 미구현 (`NotImplementedError`) | `ingest_real_estate_deals.py` | 낮음 |
| G12 | ~~`financial_statements` 잡이 스케줄러에 미등록~~ — **해소**(주간 등록) | `ingestion/scheduler.py` | ~~높음~~ |
| G13 | ~~국고채 금리 단위 불일치~~ — **해소**. `city_ai_stub.py`가 이제 DB의 KTB1Y/KTB3Y(bp, `ingest_macro_rates.py`가 이미 ×100 정규화)를 실측으로 읽어 yield_1y_bp/yield_3y_bp로 쓰고, 단위 재변환 없이 그대로 전달한다. 데이터가 없으면(백필 전 구간) 합성값으로 폴백. predicted_change_bp(63거래일 예측)는 여전히 합성 — City AI 모델 자체(G4)가 없기 때문 | `city_ai_stub.py` | ~~높음~~ |

> **G12는 최근 추가된 스케줄러의 사각지대다.** `scheduler.py`의 `_JOBS`에는
> `macro_rates`/`equity_prices`/`korean_equity_prices` 3개만 등록돼 있다.
> DART BPS 연동을 애써 구현해 두고도 자동 갱신이 안 되면, 리포트는 계속
> "보고서 고정값(DART 데이터 없음)"을 표시하게 된다. 한 줄 추가로 해결되지만
> 그 전까지는 실데이터 경로가 사실상 죽어 있는 상태다.

> **G2는 특히 위험하다.** GIPS 기준에서 실현되지 않은 성과를 실적처럼 제시하는 것은
> 단순 버그가 아니라 컴플라이언스 위반에 해당한다. 현재는 캡션에 "합성 예시"라고
> 명시해 두어 방어되고 있지만, 이 상태로 외부에 배포되면 안 된다.

---

## 1. 기관 리포트 표준 — 조사 결과

무엇을 목표로 삼을지 기준을 먼저 고정한다.

### 1.1 Bridgewater Daily Observations
- **인과 메커니즘 우선**: "무엇이 올랐다"가 아니라 "왜 그렇게 움직일 수밖에 없었나"
- 크로스에셋·레짐 관점 (주식/채권/통화/원자재를 한 프레임에서)
- 실제 운용 거래 데이터를 포함한 공개·비공개·내부 소스 결합
- 구조: executive summary → 핵심 테마 → 데이터 근거 → 리스크

### 1.2 CFA Institute 표준 리서치 리포트
필수 섹션: 기업 기본정보 → **투자 요약(실적 전망 + 밸류에이션)** → 사업 설명 →
경영진·지배구조 → 산업·경쟁 분석 → 밸류에이션 → 재무 분석·전망 → **투자 리스크**

헤더 박스 필수: 티커/거래소/섹터, 현재가/시총, **투자의견 + 목표주가 + 상승여력**,
투자기간(통상 12개월)

### 1.3 GIPS (성과 제시 표준)
- **최소 5년** 연간 수익률 (또는 설정 이후 전 기간)
- 벤치마크 수익률 **병기 필수**
- 3년 연환산 사후(ex-post) 표준편차
- 내부 분산도, 운용자산 규모, 수수료 체계, 컴포지트 정의 공시

### 1.4 국민연금 기금운용본부
- 5년 중기계획(목표수익률) → 연간 최적 자산배분 → **위험한도 내** 집행
- 투자집행 조직과 **리스크관리 조직 분리** (리스크관리위원회 독립)

### 1.5 Brinson 성과 귀속
초과수익 = **자산배분 효과(allocation)** + **종목선택 효과(selection)** + 상호작용

---

## 2. Master Plan — 5단계

각 단계는 "이전 단계 없이는 다음 단계가 무의미하다"는 순서로 배열했다.

### Phase 0 — 신뢰 기반: 숫자를 진짜로 만든다 (최우선)

**목표: 리포트의 모든 숫자가 DB의 실측 데이터에서 나온다.**

| 작업 | 산출물 | 완료 기준 |
|---|---|---|
| ~~0-1~~ | ~~인제스천 잡 프로덕션 최초 실행~~ | **완료** — 로컬 세션에서 4개 job(macro_rates/equity_prices/korean_equity_prices/financial_statements) 실제 API로 실행, `ingestion_run` 전건 success. FMP 402 차단(XLE) 발견 후 Yahoo Finance로 교체, BOK 국고채 %→bp 단위 정규화(G13) 실측 확인 |
| ~~0-2~~ | ~~시계열 히스토리 백필 (최소 5년)~~ | **완료** — KTB1Y/KTB3Y(2022-01~, 4.6년), XLE/SPY/005930/000660(2021-08~, 5.0년), DART BPS 5개 사업연도. `backfill_*` job 4개, `ingestion_router.py`에 수동 트리거로 등록(재해복구/재실행용) |
| ~~0-3~~ | ~~Point-in-time 스키마 도입~~ | **완료** — 원격 세션이 `knowledge_date` 컬럼·마이그레이션·`point_in_time.py` 구현. 로컬 세션이 DART는 rcept_no(접수번호) 파싱으로 회계연도말+90일 근사 대신 실제 공시일을 쓰도록 개선 |
| ~~0-4~~ | ~~하드코딩 성과 제거~~ | **완료** — 원격 세션에서 `BACKTEST_SUMMARY` 등 조작된 수치 제거 |
| ~~0-5~~ | ~~데이터 품질 게이트~~ | **완료** — `app/ingestion/quality.py` + `GET /ingestion/quality`. 데이터 유무·스테일·상식범위(단위 오류)·이상치·영업일 결측 검사. 실 DB `ok=true` 확인 |

**Phase 0 완료.** 실측 검증: `dim_asset` 6개, `fact_market_daily` 8,570+행,
`fact_financial_quarterly` 10행, 전부 `knowledge_date` 채워짐, 품질 게이트
통과. 전 자산 GIPS 5년 요건 충족(KTB1Y/KTB3Y 2021-01-04~, XLE/SPY/005930/
000660 2021-08-02~). 코드 리뷰 중 `backfill_macro_rates.py`의 연도 범위 계산에
off-by-one 버그를 발견해 수정 — BOK API 자체는 2020년 이전 데이터도 정상
반환하는데, `range(year-N+1, year+1)` 계산식이 5년 전 연도를 통째로 빠뜨리고
있었다(다른 백필 job들의 `range(year-N, year+1)`과 어긋남).

---

### Phase 1 — 리스크·성과 엔진

**목표: GIPS 수준의 성과 제시와 표준 리스크 지표.**

신규 모듈: `app/computation/risk/`

| 작업 | 내용 |
|---|---|
| ~~1-1~~ | ~~백테스트 엔진~~ — **완료** `backtest/engine.py` (워크포워드·비중 드리프트·거래비용·제약 연동·리밸런싱 스케줄) |
| ~~1-2~~ | ~~성과 지표~~ — **완료** `risk/metrics.py` (CAGR·변동성·Sharpe·Sortino·Calmar·MDD·회복기간) |
| ~~1-3~~ | ~~리스크 지표~~ — **완료** `risk/metrics.py` (VaR·CVaR·하방편차·베타·알파·추적오차·정보비율) |
| ~~1-4~~ | ~~GIPS 성과표~~ — **완료** `risk/gips.py` (연간수익률·벤치마크 병기·3년 ex-post 표준편차·최소이력 판정) |
| ~~1-5~~ | ~~롤링 분석~~ — **완료** `risk/rolling.py` (12M 롤링 Sharpe·변동성·수익률·MDD·상관계수·베타·추적오차) |

**1-1의 설계 원칙 — 룩어헤드를 규율이 아니라 구조로 막는다.**
비중 함수는 `returns_panel[:t]`만 받는다. 당기 이후 수익률이 배열에 아예
존재하지 않으므로, 실수든 고의든 미래를 참조할 방법이 없다(미래 행 접근 시
`IndexError`가 나는 것을 테스트로 고정). `db/point_in_time.py`의
`visible_as_of()`가 DB 계층에서 하는 일을 계산 계층에서 하는 것이다.

리밸런싱 사이 **비중 드리프트**를 반영하는 것도 같은 이유다. 드리프트를 무시하면
수익률뿐 아니라 회전율이 과소평가돼 거래비용이 싸 보이고, 결과적으로 성과가
부풀려진다 — G2가 만들어낸 것과 같은 종류의 거짓 숫자다.

**1-5는 NaN 패딩을 쓰지 않는다.** 값이 존재하는 구간만 `end_indices`와 함께
반환하고, 창 안에서 지표가 정의되지 않으면(변동성 0 구간의 Sharpe 등) 0이 아니라
`None`을 준다. 앞쪽 NaN이 차트에서 "0.00"으로 찍히는 사고를 구조적으로 막는다.

**부수 성과 — `max_drawdown` 결함 발견·수정.** 부의 경로에 기초 자본 1.0이
빠져 있어, 시작 직후 하락 구간의 낙폭이 절반 가까이 과소평가되고 있었다
(`[-10%, -10%]`는 -19%가 맞는데 -10%로 계산). 롤링 낙폭 테스트를 작성하다
실측으로 잡았다.

**리포트 반영 — CallRank·MetroGuard 완료, 밸류에이션은 해당 없음.**
`risk/report_context.py`가 DB → 성과 페이지 연결부다. CallRank·MetroGuard 둘 다
보류 페이지가 실제 성과·리스크 표 + GIPS 표 + 12개월 롤링 차트로 교체됐다(이력
부족 시에는 보류 페이지로 자동 폴백).

여기서 **CallRank가 백테스트하는 것은 리스크패리티 중립 배분이지 CallRank 전략이
아니다.** CallRank 점수는 아직 난수라(G3), 그 신호로 기울인 백테스트를 성과로
싣는 것은 제거하기로 한 G2와 같은 물건이 된다. 중립 배분은 규칙이 완전히 명시돼
있고 입력이 실제 가격뿐이라 검증 가능한 사실이며, 그 성격을 공시로 함께 싣는다.
G3가 해소되면 같은 엔진에 tilt만 추가하면 전략 백테스트가 된다.

**MetroGuard는 D*=2년(1년물·3년물 동일가중) 고정 배분을 백테스트한다.** 실제
목표 듀레이션(D*)은 City AI 예측(G4, 아직 합성)에 의존하므로 그 값을 그대로
백테스트하면 CallRank의 G3와 같은 문제가 된다. D*=2년은 예측 신호 없이 정의되는
고정점이라 검증 가능한 기준선으로 쓴다. 유니버스(통안채1년 122260·국고채3년
114260)가 자산 2개뿐이라 벤치마크(3년물 100% 고정)를 유니버스 밖으로 뺄 수 없어
`build_performance_context(..., benchmark_in_universe=True)`로 벤치마크를
유니버스에 남긴 채 별도 100% 배분 곡선으로 계산한다. 한국에는 순수 국고채 1년
ETF가 없어(국고채는 3/10/30년만 상장) 1년물 대리자산으로 통안채(발행주체가
국고채와 다름)를 쓴다 — 근거는 `ingest_korean_equity_prices.py` docstring 참고.

**밸류에이션(RIM)은 이 Phase의 대상이 아니다.** RIM은 시계열 백테스트가 아니라
"현재가 vs 확률가중 적정가"를 매 시점 독립적으로 계산하는 정적 평가 모형이라
GIPS 성과표·롤링 분석 같은 "성과 페이지" 개념 자체가 적용되지 않는다. 대신 실측
우선 원칙은 이미 지키고 있다 — 현재가(KIS)·BPS(DART) 둘 다 실측값이 있으면
그것을, 없으면 출처를 명시한 채 보고서 고정값으로 폴백한다(2026-08 실측:
삼성전자·SK하이닉스 모두 실측 경로가 실제로 채워져 있음을 확인).

---

### Phase 2 — 포트폴리오 구성

**목표: "랭킹"에서 "실행 가능한 포트폴리오"로.**

신규 모듈: `app/computation/portfolio/`

| 작업 | 내용 |
|---|---|
| ~~2-1~~ | ~~비중 산출~~ — **완료** `portfolio/weighting.py` (동일가중·역변동성·ERC 리스크패리티·신호 tilt) |
| ~~2-2~~ | ~~제약 엔진~~ — **완료** `portfolio/constraints.py` (종목 상·하한·섹터 상한·회전율 한도·교대 사영·실현가능성 검사) |
| ~~2-3~~ | ~~거래비용 모델~~ — **완료** `portfolio/costs.py` (스프레드+√시장충격·리밸런싱 손익분기) |
| ~~2-4~~ | ~~위험예산~~ — **완료** `portfolio/weighting.py` (risk_budget·위험기여 비중·섹터 위험한도 검사) |
| ~~2-5~~ | ~~Brinson 귀속~~ — **완료** `portfolio/attribution.py` (배분·선택·상호작용 분해, 항등식 검증) |

리포트 반영: **완료** — CallRank 6페이지째 "랭킹이 비중으로 바뀌는 과정"(`portfolio/report_context.py`). 가격 이력 252거래일 미만이면 합성 공분산을 만들지 않고 대기 상태로 표시.

---

### Phase 3 — 크로스에셋 레짐 (Bridgewater 차별화 지점)

**목표: 3개 리포트가 하나의 세계관을 공유한다.**

현재 CallRank(미국 주식)·MetroGuard(한국 채권)·밸류에이션(한국 반도체)은 서로
아무 대화를 하지 않는다. Bridgewater의 핵심 경쟁력이 바로 이 연결에 있다.

| 작업 | 내용 |
|---|---|
| 3-1 | **레짐 분류기** — 성장↑↓ × 인플레↑↓ 4분면 (All Weather 프레임) |
| 3-2 | **크로스에셋 상관 행렬** — 레짐별 자산군 상관 변화 |
| 3-3 | **통합 매크로 대시보드** — 3개 전략을 한 레짐 프레임에서 조망 |
| 3-4 | **인과 서술 생성** — "왜 이렇게 움직이는가"를 데이터에서 자동 도출 |

신규 리포트: **Macro Regime Observations** (4번째 리포트, 월간)

---

### Phase 4 — 기관 표준 리포트 프레임

**목표: CFA/GIPS 표준 섹션 구조 완비.**

| 작업 | 내용 |
|---|---|
| 4-1 | **Executive Summary 표준화** — 투자의견 + 목표가 + 상승여력 + 투자기간 헤더박스 |
| 4-2 | **필수 공시 페이지** — 방법론 한계, 데이터 출처, 이해상충, 면책 |
| 4-3 | **데이터 계보(lineage) 부록** — 모든 숫자의 출처·조회시점·계산식 추적 |
| 4-4 | **산업·경쟁 분석** (밸류에이션) — CFA 필수 섹션 중 현재 누락분 |
| 4-5 | **시나리오 확률 근거 문서화** — 현재 20/50/25/5%는 근거 서술이 정성적 |

---

### Phase 5 — 운영 성숙도

| 작업 | 내용 |
|---|---|
| ~~5-1~~ | ~~CI 파이프라인~~ — **완료** `.github/workflows/ci.yml` (Postgres 서비스 + 마이그레이션 왕복 + 테스트 + 리포트 렌더링) |
| ~~5-2~~ | ~~인제스천 스케줄링~~ — **완료** (`scheduler.py`, 매일 07:30 KST). 단 G12 참조: `financial_statements` 미등록 |
| ~~5-3~~ | ~~모니터링·알림~~ — **완료**. 텔레그램 봇 연동(`app/ingestion/alerting.py`) + `alert_log` 테이블. job 실패 시 즉시 알림, 일간 인제스천 뒤(07:45) 품질 게이트 자동 실행해 오류 시 알림. `GET /ingestion/alerts`로 이력 조회 |
| 5-4 | **리포트 버전 관리** — 동일 as_of 재생성 시 diff 추적 |
| 5-5 | **부동산 인제스천 구현** — 현재 `NotImplementedError` |

---

## 3. 우선순위 요약

```
지금 당장 (Phase 0)          → 숫자를 진짜로. 특히 knowledge_date 스키마.
그 다음 (Phase 1)            → 리스크·성과 엔진. GIPS 5년 성과표.
                               ↑ 여기까지가 "기관에 보여줄 수 있는" 최소선
그 다음 (Phase 2, 5)         → 포트폴리오 구성 + CI/스케줄링
차별화 (Phase 3)             → 크로스에셋 레짐. Bridgewater 수준의 분기점
마감 (Phase 4)               → 공시·계보·표준 섹션
```

**핵심 메시지**: 리포트를 30페이지로 늘리는 것보다, 지금 24페이지의 숫자를
진짜로 만드는 것이 기관 수준에 훨씬 가깝게 데려다준다.

---

## 4. 즉시 착수 가능한 다음 작업 (Phase 0 완료 후 갱신)

Phase 0(0-1~0-5)이 전부 끝났고 전 자산 GIPS 5년 요건도 충족한다. 다음 우선순위:

1. **G4 City AI 실제 모델** — `city_ai_stub.py`의 금리커브 입력은 실측으로
   교체됐으나(G13 해소), 63거래일 예측(predicted_change_bp) 자체는 여전히
   합성이다. FRED 글로벌 금리·Zillow ZHVI 등 나머지 입력 소싱이 필요해
   범위가 크므로 Phase 3(크로스에셋) 착수 전 검토 대상.
2. **G9 크로스에셋 뷰 (Phase 3)** — 3개 리포트가 서로 대화하지 않는 것이
   다음으로 큰 구조적 갭이다.

완료된 항목(재작업 불필요): 0-1~0-5 전부, 1-1~1-5(CallRank·MetroGuard 리포트
반영 포함), 2-1~2-5, 5-1(CI), 5-2(스케줄러, financial_statements 포함),
5-3(모니터링·알림), G13.

**5-3 구현 중 발견한 부수 결함 — ETF 상식범위 오탐(false positive).**
품질 게이트를 실제로 자동 실행해보니 `122260`(KIWOOM 통안채1년, 원화표시)이
매일 ERROR로 잡히고 있었다. `PLAUSIBLE_RANGES`가 통화 구분 없이 ETF 상한을
100,000으로 뒀는데, 원화 ETF는 좌수당 5~10만원대가 흔해(122260 실제 종가
103,960원) 미국 ETF(XLE $59, SPY $747) 기준을 그대로 쓰면 정상값이 범위를
넘는다. `(asset_type, currency)` 조합으로 범위를 분리해 해소 — 알림 시스템이
동작한 첫날 실제 문제를 잡아낸 사례.

---

## 부록: 참고 출처

- [Bridgewater — A Selection of Research](https://www.investmentmagazine.com.au/wp-content/uploads/2021/09/Bridgewater-Research.pdf)
- [Bridgewater Daily Observations (샘플)](https://www.bridgewater.com/_document/its-all-classic-the-main-questions-are-about-the-exact-timing-and-what-the-next-downturn-will-be-like?id=00000171-a3c7-dfa3-af71-f7d732640000)
- [CFA Institute — Elements of a Company Research Report](https://analystprep.com/cfa-level-1-exam/equity/elements-of-company-research-report/)
- [Corporate Finance Institute — Equity Research Report](https://corporatefinanceinstitute.com/resources/valuation/equity-research-report/)
- [GIPS Standards for Firms 2020](https://www.gipsstandards.org/wp-content/uploads/2021/03/2020_gips_standards_firms.pdf)
- [GIPS Reporting and Composite Presentation (CFA Level III)](https://analystprep.com/study-notes/cfa-level-iii/requirements-for-presenting-and-reporting-composites/)
- [국민연금기금 투자정책서](https://fund.nps.or.kr/fileDown.do?atchFileId=FL25001964&atchFileSn=1)
- [국민연금기금운용본부 포트폴리오 현황](https://fund.nps.or.kr/oprtprcn/ivsmprcn/getOHED0016M0.do)
- [SimCorp — Risk-based or Brinson attribution](https://www.simcorp.com/resources/insights/industry-articles/2024/Risk-based-or-Brinson-attribution)
- [Performance Evaluation and Attribution (CFA Level III)](https://analystprep.com/study-notes/cfa-level-iii/performance-evaluation/)
