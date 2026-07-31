from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = "postgresql+psycopg2://report_agent:report_agent@localhost:5432/report_agent"
    api_key: str = "changeme"

    # 서빙 캐시(Supabase) 동기화용. 비워두면 로컬 전용으로 동작하고 동기화는 조용히 스킵된다.
    supabase_url: str | None = None
    supabase_service_key: str | None = None

    # PDF 저장용 Cloudflare R2(S3 호환). 비워두면 조용히 스킵되고 PDF는 로컬 서빙만 된다.
    r2_account_id: str | None = None
    r2_access_key_id: str | None = None
    r2_secret_access_key: str | None = None
    r2_bucket_name: str | None = None
    r2_public_base_url: str | None = None  # 커스텀 도메인 또는 <bucket>.r2.dev

    # 실데이터 인제스천용(ingestion/connectors).
    dart_api_key: str | None = None  # 금융감독원 전자공시(DART) OpenAPI
    bok_api_key: str | None = None  # 한국은행 ECOS OpenAPI
    kis_app_key: str | None = None  # 한국투자증권 OpenAPI
    kis_app_secret: str | None = None
    kis_account_number: str | None = None
    kis_base_url: str = "https://openapivts.koreainvestment.com:29443"  # 모의투자 기본값
    kis_use_mock: bool = True
    fred_api_key: str | None = None  # FRED(미국 연준) — City AI의 글로벌 금리 입력
    fmp_api_key: str | None = None  # Financial Modeling Prep — 시세·실적발표 transcript

    # .env는 커밋되는 예시/기본값용, .env.local은 실제 비밀값(gitignore 대상)용.
    # 두 파일 다 있으면 .env.local이 나중에 로드되어 .env 값을 덮어쓴다.
    model_config = SettingsConfigDict(
        env_file=(".env", ".env.local"), env_file_encoding="utf-8", env_prefix="REPORT_AGENT_"
    )


settings = Settings()
