from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = "postgresql+psycopg2://report_agent:report_agent@localhost:5432/report_agent"
    api_key: str = "changeme"

    model_config = SettingsConfigDict(env_file=".env", env_prefix="REPORT_AGENT_")


settings = Settings()
