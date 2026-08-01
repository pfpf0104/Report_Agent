"""Settings가 REPORT_AGENT_ 접두사 없는 무관한 키와 같은 .env 파일을 공유해도
기동에 실패하지 않는지 확인한다.

실제로 로컬 PC의 .env.local이 다른 프로젝트와 공유하는 TELEGRAM_*, KIS_PAPER_*
같은 키들과 같이 있어, extra="ignore"가 없던 시절에는 Settings() 생성 자체가
ValidationError로 즉시 실패해 앱이 기동되지 않는 것으로 재현·확인됐다.
"""
from pydantic_settings import SettingsConfigDict

from app.core.config import Settings


def test_settings_ignores_env_vars_without_matching_prefix(tmp_path, monkeypatch):
    env_file = tmp_path / ".env.local"
    env_file.write_text(
        "REPORT_AGENT_API_KEY=real-key\n"
        "TELEGRAM_BOT_TOKEN=unrelated-value\n"
        "KIS_PAPER_APP_KEY=unrelated-value\n",
        encoding="utf-8",
    )

    class _TestSettings(Settings):
        model_config = SettingsConfigDict(
            env_file=str(env_file), env_file_encoding="utf-8", env_prefix="REPORT_AGENT_", extra="ignore"
        )

    settings = _TestSettings()
    assert settings.api_key == "real-key"
