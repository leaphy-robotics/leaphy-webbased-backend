"""Configuration settings"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Settings class, loaded from .env or environment vars"""

    model_config = SettingsConfigDict(env_file=".env")

    platformio_data_dir: str = "./compiles"
    threads_per_platformio_compile: int = 1
    cors_origins: list[str] = ["*"]
    cors_origin_regex: str | None = None
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

    # Max number of concurrent compile tasks
    max_concurrent_tasks: int = 10

    # Groq config
    groq_api_key: str
    max_llm_tokens: int = 10000


settings = Settings()
