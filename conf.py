""" Configuration settings """
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Settings class, loaded from .env or environment vars"""

    model_config = SettingsConfigDict(env_file=".env")

    arduino_cli_path: str = "arduino-cli"
    cors_origins: list[str] = ["*"]

    # Session settings
    max_sessions_per_user: int = 1
    max_total_sessions: int = 10000
    session_duration: int = 3600

    # Code cache settings
    max_code_caches: int = 100
    code_cache_duration: int = 3600
    # Max number of concurrent compile tasks
    max_concurrent_tasks: int = 10


settings = Settings()
