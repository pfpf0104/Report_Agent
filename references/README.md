# References — 기관 리포트 표준 참고문헌

`docs/MASTER_PLAN.md`의 근거 자료. 각 문서가 이 프로젝트의 어떤 결정에
쓰였는지를 함께 기록한다.

## 다운로드

```bash
bash references/download_references.sh
```

> **원격 개발 샌드박스에서는 실행되지 않는다.** 아웃바운드 네트워크 정책이
> 이 호스트들을 차단해(`curl`·WebFetch 모두 CONNECT 403) PDF를 받을 수 없다.
> 네트워크 제약이 없는 로컬 PC에서 실행할 것. 이 프로젝트가 DART/BOK/FMP
> 라이브 검증을 로컬 세션에 위임하는 것과 같은 이유다.

받은 PDF는 `.gitignore` 대상이다(저작권 문서를 저장소에 커밋하지 않는다).
링크가 깨졌으면 아래 표의 출처에서 직접 검색해 받으면 된다.

---

## 1. 성과 제시 표준 (GIPS)

| 파일 | 출처 |
|---|---|
| `gips_standards_for_firms_2020.pdf` | [GIPS Standards for Firms 2020](https://www.gipsstandards.org/wp-content/uploads/2021/03/2020_gips_standards_firms.pdf) |
| `gips_fiduciary_management_handbook.pdf` | [GIPS for Fiduciary Management](https://www.gipsstandards.org/wp-content/uploads/2021/06/gips-standards-fmp-handbook.pdf) |

**이 프로젝트에서의 용도**: `app/computation/performance_disclosure.py`의
`GIPS_REQUIREMENTS`가 여기서 나왔다. 최소 5년 연간 수익률, 벤치마크 병기,
3년 연환산 사후 표준편차, 내부 분산도, 컴포지트 정의·수수료 체계 공시.

성과 페이지에서 난수 기반 수치(42.1%, Sharpe 2.05)를 제거한 근거이기도 하다 —
실현되지 않은 성과를 실적처럼 제시하는 것은 이 표준이 금지한다.

관련: [GIPS Reporting and Composite Presentation (CFA Level III)](https://analystprep.com/study-notes/cfa-level-iii/requirements-for-presenting-and-reporting-composites/)

---

## 2. 리서치 리포트 구조 (Bridgewater)

| 파일 | 출처 |
|---|---|
| `bridgewater_selection_of_research.pdf` | [A Selection of Bridgewater Research](https://www.investmentmagazine.com.au/wp-content/uploads/2021/09/Bridgewater-Research.pdf) |
| `bridgewater_daily_observations_sample.pdf` | [Daily Observations 샘플](https://economicprinciples.org/downloads/bwam102317.pdf) |

**이 프로젝트에서의 용도**: MASTER_PLAN Phase 3(크로스에셋 레짐)의 근거.
Bridgewater의 차별점은 "무엇이 올랐다"가 아니라 **"왜 그렇게 움직일 수밖에
없었나"**를 인과 메커니즘으로 서술하고, 주식·채권·통화·원자재를 하나의 레짐
프레임에서 본다는 점이다.

현재 이 프로젝트의 3개 리포트(CallRank/MetroGuard/밸류에이션)는 서로 대화하지
않는다 — 이 격차를 메우는 것이 Phase 3다.

추가 샘플: [Bridgewater 공식 문서](https://www.bridgewater.com/_document/its-all-classic-the-main-questions-are-about-the-exact-timing-and-what-the-next-downturn-will-be-like?id=00000171-a3c7-dfa3-af71-f7d732640000)

---

## 3. 연기금 운용 프레임 (국민연금)

| 파일 | 출처 |
|---|---|
| `nps_investment_policy_statement.pdf` | [국민연금기금 투자정책서](https://fund.nps.or.kr/fileDown.do?atchFileId=FL25001964&atchFileSn=1) |
| `nps_fund_management_report_2025.pdf` | [2025 기금운용 보고서](https://www.nps.or.kr/html/download/management/2025_u_report_1.pdf) |

**이 프로젝트에서의 용도**: MASTER_PLAN Phase 2(포트폴리오 구성)의 근거.
핵심 구조는 **5년 중기계획(목표수익률) → 연간 최적 자산배분 → 위험한도 내
집행**이며, 투자집행 조직과 리스크관리 조직이 분리돼 있다(리스크관리위원회 독립).

MetroGuard의 "AI 자본권한은 단축으로만 제한"(FORMULA_CARDS의 AUTHORITY 항목)이
같은 발상 — 모델에 무제한 권한을 주지 않고 방향과 한도를 미리 못박는다.

참고: [포트폴리오 현황](https://fund.nps.or.kr/oprtprcn/ivsmprcn/getOHED0016M0.do)

---

## 4. 성과 귀속·리스크 (Brinson, Drawdown)

| 파일 | 출처 |
|---|---|
| `performance_attribution_equity_portfolios.pdf` | [Performance Attribution for Equity Portfolios (Lu & Kane)](https://cran.r-project.org/web/packages/pa/vignettes/pa.pdf) |
| `drawdown_from_practice_to_theory.pdf` | [Drawdown: From Practice to Theory and Back Again](https://arxiv.org/pdf/1404.7493) |

**이 프로젝트에서의 용도**: MASTER_PLAN Phase 1(리스크 지표)·Phase 2-5(Brinson
귀속)의 근거.

Brinson(1986) 프레임: 초과수익 = **자산배분 효과** + **종목선택 효과** + 상호작용.
CallRank는 현재 섹터 랭킹만 내놓고 그 랭킹이 실제 초과수익에 어떻게 기여했는지
분해하지 않는다.

웹 문서: [SimCorp — Risk-based or Brinson attribution](https://www.simcorp.com/resources/insights/industry-articles/2024/Risk-based-or-Brinson-attribution) ·
[Performance Evaluation and Attribution (CFA Level III)](https://analystprep.com/study-notes/cfa-level-iii/performance-evaluation/)

---

## 5. 리서치 리포트 필수 섹션 (CFA) — 웹 문서

PDF 배포본이 없어 링크로만 보관한다.

- [Elements of a Company Research Report (CFA Level I)](https://analystprep.com/cfa-level-1-exam/equity/elements-of-company-research-report/)
- [Equity Research Report: Definition, Types, Key Components (CFI)](https://corporatefinanceinstitute.com/resources/valuation/equity-research-report/)
- [Standard V — Investment Analysis, Recommendations, and Action](https://analystprep.com/study-notes/cfa-level-iii/standard-v-investment-analysis-recommendations-and-action/)

**이 프로젝트에서의 용도**: MASTER_PLAN Phase 4의 근거.

필수 섹션: 기업 기본정보 → 투자 요약(실적 전망 + 밸류에이션) → 사업 설명 →
경영진·지배구조 → **산업·경쟁 분석** → 밸류에이션 → 재무 분석·전망 → 투자 리스크.

헤더 박스 필수: 티커/거래소/섹터, 현재가/시총, **투자의견 + 목표주가 + 상승여력**,
투자기간(통상 12개월).

현재 밸류에이션 리포트에 누락된 것: 산업·경쟁 분석 섹션, 표준 헤더 박스
(투자의견·목표주가·투자기간이 명시적 필드로 없음).
