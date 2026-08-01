# Report Agent

세 가지 금융 리포트(CallRank 섹터 로테이션, MetroGuard-KR 채권 듀레이션, 반도체
기업 RIM 밸류에이션)를 FastAPI + PostgreSQL + WeasyPrint로 PDF까지 자동 생성하는
파이프라인. 무거운 연산·인제스천은 로컬 PC에서 돌리고, Supabase(서빙 캐시)·
Cloudflare R2(PDF 저장)는 선택적으로만 붙인다 — 둘 다 비워두면 로컬 전용으로
정상 동작한다.

## 로컬 환경 준비

```bash
python -m pip install -r requirements.txt
cp .env.example .env.local   # 실제 키/비밀값은 .env.local에만 채운다(gitignore 대상)
alembic upgrade head
```

`.env.local`에 관해:
- `REPORT_AGENT_` 접두사가 붙은 키만 이 앱이 읽는다. 다른 프로젝트와 `.env.local`을
  공유해도(예: `TELEGRAM_BOT_TOKEN`, `KIS_PAPER_*` 등 무관한 키가 섞여 있어도)
  `app/core/config.py`의 `Settings`는 접두사가 안 맞는 키를 조용히 무시한다.
- `REPORT_AGENT_DATABASE_URL`은 반드시 `postgresql+psycopg2://` 스킴을 써야 한다
  (`postgresql+asyncpg://`나 스킴 없는 `postgresql://`을 쓰면 드라이버 불일치로
  기동 실패한다 — `app/db/base.py`가 SQLAlchemy 동기 엔진을 쓴다).

## WeasyPrint (PDF 렌더링) — Windows 개발 환경 주의

WeasyPrint는 GTK 계열 네이티브 라이브러리(`libgobject-2.0-0` 등)에 의존한다.
Windows에 이 네이티브 라이브러리가 없으면 PDF를 렌더링하는 순간(`/reports/...`
엔드포인트 호출 시) 에러가 난다. `/health`, `/ingestion` 라우터는 weasyprint를
전혀 쓰지 않으므로 그 두 라우터만 쓸 때는 GTK 없이도 정상 기동된다.

Windows에서 실제로 PDF 렌더링까지 로컬 개발하려면 다음 중 하나를 권장한다:
- **WSL2**: WSL2 안의 Ubuntu에서 이 저장소를 클론하고 `apt install`로 GTK
  런타임을 설치한 뒤 그 안에서 실행한다.
- **Docker**: 리눅스 베이스 이미지에 GTK 런타임을 설치한 컨테이너에서 실행한다.

## 테스트

```bash
python -m pytest tests -q
```

유닛 테스트는 네트워크를 respx로 목(mock)하고, 통합 테스트는 로컬 PostgreSQL에
실제로 쓴다(로컬 Postgres가 떠 있어야 한다). 목 테스트가 전부 통과해도 외부 API의
실제 엔드포인트/파라미터/스키마가 바뀌었을 수 있으므로, `scripts/verify_external_apis.py`
로 주기적으로 실제 키 기반 라이브 검증을 병행한다.

## 실데이터 연동 상태

`app/ingestion/connectors/`의 DART·BOK ECOS·FRED·FMP·KIS 클라이언트는 전부
실제 API 키로 라이브 검증을 거쳤다. 세부 사항은 각 커넥터/잡 파일의 docstring을
참고한다.

## PDF 자산화 + Cross-check 검증 (`app/extraction`, `app/validation`)

임의의 PDF(재무제표·기업정보 리포트 등)를 업로드하면 텍스트/표에서 숫자를
추출해 `extracted_document`/`extracted_value` 테이블에 자산화하고, 각 값을
Cross-check 엔진으로 검증한다. `POST /extraction/upload`로 업로드하고
`GET /extraction/documents/{id}`로 결과를 조회한다(`X-API-Key` 헤더 필요).

- **추출**: `app/extraction/pdf_parser.py`가 pdfplumber로 텍스트 레이어를 먼저
  시도하고, 페이지의 텍스트가 부족하면(스캔본) pytesseract+pdf2image로 OCR
  폴백한다. `app/extraction/number_extractor.py`가 "라벨 + 숫자(+단위)" 후보를
  뽑는다 — FnGuide류 PDF의 "단위 : 억원" 섹션 헤더를 인식해 원 단위로 정규화한다
  (실제 SK하이닉스 재무제표 PDF로 검증: 정규화 전에는 DART 실측치와 자릿수가
  달라 mismatch로 잘못 판정되던 것을 수정해 verified로 확인함).
- **검증**: `app/validation/engine.py`가 내부 체커(DART 등 이미 연동된 실데이터,
  `app/validation/checkers/internal_checkers.py`)를 먼저 시도하고, 담당 범위
  밖이면 외부 웹 검색 체커(`web_search_checker.py`)로 폴백한다. 웹 검색은
  `WebSearchProvider` 인터페이스만 정의돼 있고 실제 프로바이더(Google/Bing 등)는
  아직 미구현 — API 키 발급 전까지는 `check_failed`로 남아 "사람이 확인해야
  할 값"으로 분류된다.
- **검증 상태 4종**: `verified`(허용오차 이내 일치) / `mismatch`(대조했지만
  어긋남 — 최우선 검토 대상) / `check_failed`(대조를 시도했지만 소스를 못 찾음) /
  `unverified`(대조 자체를 안 함). `GET /extraction/documents/{id}` 응답은
  `verified_values`와 `needs_review_values`(mismatch/check_failed/unverified 합산)로
  분리해 반환한다.

**시스템 의존성**: OCR 폴백을 쓰려면 파이썬 패키지 외에 Tesseract OCR
실행파일과 poppler(pdf2image가 내부적으로 사용)가 시스템에 설치돼 있어야 한다.
텍스트 레이어가 있는 PDF만 다룬다면 이 둘 없이도 정상 동작한다.
