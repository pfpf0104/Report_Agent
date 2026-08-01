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
