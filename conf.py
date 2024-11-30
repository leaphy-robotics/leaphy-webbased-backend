""" Configuration settings """

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Settings class, loaded from .env or environment vars"""

    model_config = SettingsConfigDict(env_file=".env")

    arduino_cli_path: str = "arduino-cli"
    cors_origins: list[str] = ["*"]
    log_level: str = "INFO"

    # Session settings
    max_sessions_per_user: int = 1
    max_total_sessions: int = 10000
    session_duration: int = 3600

    # Code and library cache settings
    max_code_caches: int = 100
    code_cache_duration: int = 3600
    max_library_caches: int = 50
    library_cache_duration: int = 24 * 3600
    library_index_refresh_interval: int = 3600
    ignored_libraries: list[str] = [
        ""
    ]  # Libraries that should be ignored when downloading them. Useful for testing with unpublished changes to libraries

    # Max number of concurrent compile tasks
    max_concurrent_tasks: int = 10

    # Groq config
    groq_api_key: str
    max_llm_tokens: int = 10000


settings = Settings()
