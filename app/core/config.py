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

    model_config = SettingsConfigDict(env_file=".env", env_prefix="REPORT_AGENT_")


settings = Settings()
