# 로컬 세션 프롬프트 — Phase 0: 실데이터 적재와 5년 백필

`docs/MASTER_PLAN.md` Phase 0의 나머지(0-1, 0-2, 0-5)를 수행하기 위한 프롬프트다.
0-3(knowledge_date 스키마)과 0-4(하드코딩 성과 제거)는 원격 세션에서 이미 완료됐다.

**이 작업은 반드시 네트워크 제약이 없는 로컬 PC에서 해야 한다** — 원격 샌드박스는
DART/BOK/FRED/FMP/KIS 도메인에 CONNECT 단계에서 403으로 차단된다.

아래 블록을 그대로 복사해 로컬 Claude Code 세션에 붙여넣으면 된다.

---

```
당신은 Report_Agent 프로젝트(로컬 PC의 이 저장소)에서 작업하는 개발 에이전트다.
MASTER_PLAN.md Phase 0의 남은 작업 — 실데이터 최초 적재와 5년 히스토리 백필 —
을 수행한다.

## 배경: 왜 이 작업이 최우선인가

리포트 3종(CallRank 5p / MetroGuard 8p / 밸류에이션 11p)은 시각적으로 완성돼
있지만, 로컬 DB를 조회해보면 모든 fact 테이블이 비어 있다. 즉 지금 렌더링되는
모든 숫자는 합성 데이터이거나 코드에 박힌 상수다. 기관 수준과의 격차는 페이지
수가 아니라 "숫자가 진짜가 아니라는 것"에 있다.

시작 전에 직접 확인하라:
  SELECT 'dim_asset' t, count(*) FROM dim_asset
  UNION ALL SELECT 'fact_market_daily', count(*) FROM fact_market_daily
  UNION ALL SELECT 'fact_financial_quarterly', count(*) FROM fact_financial_quarterly
  UNION ALL SELECT 'ingestion_run', count(*) FROM ingestion_run;

## 사전 준비

1. `.env.local`에 DART/BOK/FRED/FMP/KIS 키가 모두 채워져 있는지 확인한다
   (비어 있으면 중단하고 사용자에게 알린다 — 임의로 값을 만들지 말 것).
2. `python -m pip install -r requirements.txt`
3. `alembic upgrade head` — knowledge_date 마이그레이션(c81f3a5e2d47)이 적용되는지
   확인한다. 적용 후 아래가 13개 행을 반환해야 한다(파티션 자식 포함):
     SELECT table_name FROM information_schema.columns WHERE column_name='knowledge_date';
4. `python -m pytest tests -q` — 97개 전부 통과하는지 먼저 확인한다.

## 1단계 — 인제스천 최초 실행 (Phase 0-1)

4개 잡을 순서대로 실제 실행한다. `/ingestion/trigger/{job}` 엔드포인트를 쓰거나
파이썬에서 직접 `.run()`을 호출해도 된다.

  - macro_rates            (BOK  → KTB1Y/KTB3Y)
  - equity_prices          (FMP  → XLE/SPY)
  - korean_equity_prices   (KIS  → 005930/000660)
  - financial_statements   (DART → 삼성전자/SK하이닉스 BPS)

각 실행 후 반드시 확인할 것:
  - `SELECT * FROM ingestion_run ORDER BY id DESC LIMIT 10;` — status가 전부 success인지
  - 적재된 값이 상식적인 범위인지 (삼성전자 주가가 10만~30만원대인지, 국고채
    금리가 2~4% 대인지, BPS가 수만 원대인지). 자릿수가 이상하면 단위 변환
    버그를 의심하라 — 특히 BOK은 %인지 bp인지, KIS는 문자열로 오는 숫자를
    어떻게 캐스팅하는지 확인할 것.

**중요**: `knowledge_date`가 각 행에 제대로 채워졌는지 확인하라. 이 컬럼이
NULL이면 insert가 실패했어야 정상이다(NOT NULL 제약).

## 2단계 — 5년 히스토리 백필 (Phase 0-2)

GIPS는 최소 5년 연간 수익률을 요구한다. 현재 인제스천 잡들은 "오늘 시점"만
가져오도록 짜여 있어 히스토리가 쌓이지 않는다. 백필 경로를 만들어야 한다.

구현 시 반드시 지킬 것:

  - **knowledge_date를 사건일로 채우지 말 것.** 과거 시세를 오늘 백필하면
    "그 시점에 알 수 있었나"와 "지금 알게 됐나"가 다르다. 일별 종가처럼 당일
    공표되는 데이터는 knowledge_date = trade_date가 맞지만, 정정 공시분이나
    재무제표는 실제 공시일을 써야 한다. app/db/point_in_time.py의 docstring
    참고.
  - **API 쿼터를 고려하라.** FRED/FMP/BOK 모두 호출 한도가 있다. 5년치를 한
    번에 긁지 말고 배치로 나누고, 실패 시 재개 가능하게(이미 적재된 구간은
    건너뛰도록) 만들어라.
  - 기존 `track_ingestion_run` 패턴을 그대로 쓴다.

백필 대상 우선순위:
  1. KTB1Y/KTB3Y (MetroGuard의 실제 입력)
  2. XLE/SPY 및 나머지 섹터 ETF (CallRank의 벤치마크)
  3. 삼성전자/SK하이닉스 일별 종가 + 연도별 BPS (밸류에이션)

## 3단계 — 데이터 품질 게이트 실행 (Phase 0-5)

**품질 게이트는 이미 구현돼 있다**(`app/ingestion/quality.py`). 새로 만들지 말고
적재 직후 실행해 결과를 확인하라:

    GET /ingestion/quality?as_of=YYYY-MM-DD      (X-API-Key 헤더 필요)

또는 파이썬에서:

    from app.ingestion.quality import run_quality_gate
    report = run_quality_gate(db, as_of=date.today())
    print(report.summary())
    for issue in report.issues: print(issue)

검사 항목: 데이터 유무 / 스테일 / 상식 범위(단위 오류) / 이상치 / 영업일 결측.

**반드시 확인할 것 — 국고채 금리 단위**:

`ingest_macro_rates.py`는 BOK ECOS가 주는 값을 그대로 저장한다. BOK은 금리를
**퍼센트**로 준다("2.659" = 2.659%). 그런데
`duration_controller.compute_carry_price_gate()`의 파라미터는 `yield_3y_bp`,
`yield_1y_bp` — **베이시스포인트**를 기대한다(265.9가 맞는 값).

지금은 `city_ai_stub`이 300.0(=300bp)을 공급해 이 불일치가 드러나지 않지만,
실제 BOK 데이터를 컨트롤러에 연결하는 순간 **100배 오류**가 난다. 2.659는 그
자체로 그럴듯한 숫자라 예외가 나지 않고 조용히 틀린 목표 듀레이션을 만든다.

품질 게이트가 이 오류를 잡도록 만들어져 있다(MACRO 상식 범위 10~2000bp).
KTB1Y/KTB3Y 적재 후 게이트를 돌려 `value_range` 오류가 뜨는지 확인하고,
뜬다면 다음 중 하나로 규약을 통일하라:

  - (권장) 인제스천에서 bp로 변환해 저장 — `yield_value * 100`, 그리고
    `ingest_macro_rates.py` docstring에 단위 규약을 명시
  - 또는 컨트롤러에 넘기는 경계 함수에서 명시적으로 변환

어느 쪽이든 **단위 규약을 코드 주석과 테스트로 못박아라.** 이 프로젝트가 이미
FMP 엔드포인트 폐지·BOK 통계코드 오류를 겪은 것과 같은 계열의 함정이다.

## 4단계 — 리포트 재생성 및 검증

실데이터가 들어간 상태로 3종 리포트를 다시 렌더링하고, **직접 PDF를 열어
눈으로 확인하라**.

특히 확인할 것:
  - 밸류에이션 리포트의 BPS 출처 캡션이 "보고서 고정값(DART 데이터 없음)"에서
    "DART 20XX년 사업보고서 실측 BPS"로 바뀌었는지
  - 현재가 출처가 "KIS 실시간 시세"로 바뀌었는지
  - CallRank의 MTD 수익률이 "데이터 없음"에서 실제 숫자로 바뀌었는지
  - 실데이터 BPS로 계산된 적정가가 상식적인 범위인지 (기존 폴백 대비 크게
    벗어나면 단위나 발행주식총수를 의심하라)

## 지켜야 할 규칙 (이 프로젝트의 기존 컨벤션)

- 코드를 고치면 셀프 리뷰 후 `python -m pytest tests -q`와
  `python -m compileall app`을 통과시키고 나서 커밋한다.
- 커밋 전 `git diff | grep -iE "api_key|secret|token|password"`로 시크릿이
  스테이징되지 않았는지 확인한다. `.env.local`은 절대 커밋 대상이 아니다.
- 검증하지 않은 것을 검증했다고 쓰지 않는다. 확인 못 한 가정은 코드에
  `TODO(확인 필요)`로 남긴다 — 이 프로젝트는 그 규율로 여러 실제 버그를
  잡아왔다(FMP 엔드포인트 폐지, BOK 통계코드 오류 등).
- main 브랜치에 직접 커밋/푸시한다.

## 결과 보고 (이 양식 그대로 채워서 돌려줄 것)

작업이 끝나면 아래 양식을 **그대로 복사해 채운 뒤 한국어로** 보고하라. 이 보고는
원격 세션이 다음 단계(Phase 1 성과 엔진 연결)를 설계하는 입력이 되므로, 빈칸을
추측으로 채우지 말고 확인하지 못한 항목은 "미확인"이라고 쓸 것.

```
### 1. 적재 결과
| 테이블 | 행 수 | 기간(최초~최종) | 소스 | knowledge_date 채워짐 |
|---|---|---|---|---|
| dim_asset | | - | | - |
| fact_market_daily | | | | |
| fact_financial_quarterly | | | | |

### 2. 품질 게이트 결과
GET /ingestion/quality?as_of=<오늘> 실행 결과:
  ok: <true/false>
  오류: <목록 또는 "없음">
  경고: <목록 또는 "없음">

### 3. 국고채 단위 규약 (G13)
  - value_range 오류 발생 여부: <예/아니오>
  - 채택한 규약: <bp 저장 / 경계 변환 / 기타>
  - 변경한 파일:
  - 검증 방법:

### 4. 발견한 문제와 조치
| 문제 | 위치 | 조치 | 검증 방법 |
|---|---|---|---|

### 5. 리포트 숫자 전/후 대조
| 항목 | 이전(폴백) | 이후(실데이터) |
|---|---|---|
| 삼성전자 BPS 출처 | 보고서 고정값(DART 데이터 없음) | |
| 삼성전자 장부가치 | 81,500원 | |
| 삼성전자 확률가중 적정가 | 228,043원 | |
| 삼성전자 현재가 출처 | 보고서 고정값(KIS 데이터 없음) | |
| CallRank MTD 수익률 | 데이터 없음 | |
| MetroGuard D* | (city_ai_stub 합성) | |

### 6. 여전히 합성/자리표시로 남은 것
  - sector_embeddings.py: <상태>
  - city_ai_stub.py: <상태>
  - 기타:

### 7. Phase 1(성과 엔진) 연결에 필요한 정보
  - 월간 수익률 시계열을 만들 수 있는 자산과 기간:
  - 벤치마크로 쓸 수 있는 자산:
  - GIPS 5년 요건 충족 가능 여부:
```

### 왜 7번을 묻는가

원격 세션이 이미 Phase 1의 성과·리스크 지표 엔진을 구현해 뒀다
(`app/computation/risk/metrics.py`, `gips.py`). 순수 함수라 데이터 없이도
검증은 끝났지만, 실제 리포트에 붙이려면 **어떤 자산의 몇 년치 월간 수익률이
확보됐는지**를 알아야 한다. GIPS는 최소 5년을 요구하므로 그에 못 미치면
성과 페이지는 계속 비워 두는 것이 맞다.
```

---

## 원격/로컬 작업 분담 현황

이 프로젝트는 네트워크 접근 가능 여부로 작업을 나눈다.

| 작업 | 담당 | 상태 |
|---|---|---|
| Phase 0-1 최초 적재 | **로컬**(네트워크 필요) | 이 프롬프트 |
| Phase 0-2 5년 백필 | **로컬**(네트워크 필요) | 이 프롬프트 |
| Phase 0-3 knowledge_date 스키마 | 원격 | ✅ 완료 |
| Phase 0-4 하드코딩 성과 제거 | 원격 | ✅ 완료 |
| Phase 0-5 품질 게이트 | 원격 | ✅ 완료 — 실행만 하면 됨 |
| Phase 1 성과·리스크 지표 엔진 | 원격 | ✅ 완료(순수 함수) |
| Phase 1 리포트 연결 | 원격 | 이 프롬프트의 7번 보고 대기 |
| Phase 5-1 CI | 원격 | ✅ 완료 |

원격 세션은 순수 계산·스키마·문서를 맡고, 로컬 세션은 **실제 외부 API 호출이
필요한 것만** 맡는다. 로컬 세션의 시간이 이 프로젝트에서 가장 희소한 자원이므로,
네트워크가 필요 없는 작업을 로컬에서 하지 말 것.

## 이 작업이 끝나면

Phase 0이 닫히고, 위 7번 보고를 근거로 원격 세션이 Phase 1 성과 엔진을 리포트에
연결한다. GIPS 5년 요건이 충족되면 제거했던 성과 페이지가 실제 계산값으로
복원된다(`app/computation/performance_disclosure.py` 참고).
