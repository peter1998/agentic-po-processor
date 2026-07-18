"""Central settings, loaded once from environment variables / .env.
Import `settings` from here everywhere else — single source of truth for env vars."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    anthropic_api_key: str
    voyage_api_key: str

    langchain_tracing_v2: bool = True
    langchain_api_key: str = ""
    langchain_project: str = "flatrock-po-agent"

    api_key: str

    database_path: str = "./data/purchase_orders.db"
    chroma_persist_path: str = "./data/chroma_db"

    # Kept out of code per ADR-003 — tunable without touching gate logic.
    gate1_completeness_threshold: float = 0.6
    gate1_max_retries: int = 1
    gate2_validation_rate_threshold: float = 0.75


settings = Settings()