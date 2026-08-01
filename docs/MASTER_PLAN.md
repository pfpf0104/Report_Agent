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
| G13 | **국고채 금리 단위 불일치** — BOK은 퍼센트(`2.659`)를 저장하는데 `duration_controller`는 bp(`265.9`)를 기대한다. 현재는 `city_ai_stub`이 bp를 공급해 드러나지 않지만, 실데이터 연결 시 100배 오류가 조용히 발생한다. 품질 게이트가 잡도록 해뒀으나 규약 통일은 미완 | `ingest_macro_rates.py` ↔ `duration_controller.py` | 높음 |

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
| 0-1 | 인제스천 잡 프로덕션 최초 실행 | 5개 테이블 전부 non-zero, `ingestion_run` 전건 success |
| 0-2 | 시계열 히스토리 백필 (최소 5년) | GIPS 5년 요건 충족. FRED/BOK/KIS 과거분 |
| 0-3 | **Point-in-time 스키마 도입** | 모든 fact 테이블에 `knowledge_date`(정보 취득시점) 추가, 조회 시 `as_of` 기준 필터 강제 |
| 0-4 | 하드코딩 성과 제거 | `BACKTEST_SUMMARY` 삭제 → 실제 계산 결과로 대체하거나 섹션 자체 제거 |
| ~~0-5~~ | ~~데이터 품질 게이트~~ | **완료** — `app/ingestion/quality.py` + `GET /ingestion/quality`. 데이터 유무·스테일·상식범위(단위 오류)·이상치·영업일 결측 검사 |

> **0-3이 이 프로젝트의 진짜 분기점이다.** 지금 리포트 본문은 "7일 embargo",
> "정보 동결" 같은 기관급 규율을 주장하는데 스키마가 이를 강제하지 못한다.
> `knowledge_date` 없이는 look-ahead bias를 구조적으로 막을 수 없고,
> look-ahead가 있는 백테스트는 기관 심사를 통과할 수 없다.

**Phase 0 완료 없이 Phase 1 이후를 진행하면 안 된다** — 가짜 데이터 위에 정교한
리스크 엔진을 얹어봐야 정교한 가짜가 될 뿐이다.

---

### Phase 1 — 리스크·성과 엔진

**목표: GIPS 수준의 성과 제시와 표준 리스크 지표.**

신규 모듈: `app/computation/risk/`

| 작업 | 내용 |
|---|---|
| 1-1 | **백테스트 엔진** (`backtest/engine.py`) — 워크포워드, 거래비용, 슬리피지, 리밸런싱 |
| ~~1-2~~ | ~~성과 지표~~ — **완료** `risk/metrics.py` (CAGR·변동성·Sharpe·Sortino·Calmar·MDD·회복기간) |
| ~~1-3~~ | ~~리스크 지표~~ — **완료** `risk/metrics.py` (VaR·CVaR·하방편차·베타·알파·추적오차·정보비율) |
| ~~1-4~~ | ~~GIPS 성과표~~ — **완료** `risk/gips.py` (연간수익률·벤치마크 병기·3년 ex-post 표준편차·최소이력 판정) |
| 1-5 | **롤링 분석** — 12M 롤링 Sharpe/변동성/상관계수 차트 |

리포트 반영: 3종 전부에 "성과·리스크" 페이지를 실제 계산 결과로 교체.

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
| 5-3 | **모니터링·알림** — 인제스천 실패, 데이터 스테일, 이상치 감지 |
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

## 4. 즉시 착수 가능한 다음 3개 작업

0. **`financial_statements`를 스케줄러에 등록** — 한 줄. G12 해소. 이걸 안 하면
   DART BPS 연동이 운영에서 죽은 코드로 남는다. (Phase 0, 5분)
1. **`knowledge_date` 스키마 마이그레이션** — 모든 fact 테이블에 정보취득시점 컬럼
   추가 + 조회 계층에 `as_of` 필터 강제. (Phase 0-3, 다른 모든 것의 전제조건)
2. **하드코딩 성과 제거** — `BACKTEST_SUMMARY` 3개 값을 실제 계산으로 대체하거나
   제거. (Phase 0-4, 컴플라이언스 리스크 해소)
3. **CI 파이프라인** — 92개 테스트 자동화. (Phase 5-1, 이번 세션에서 발견한
   버그 유형이 조용히 재발하는 것 방지)

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
