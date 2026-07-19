"""
Central settings, loaded once from environment variables / .env.
Every other module imports `settings` from here instead of calling
os.environ directly — one source of truth, no magic strings scattered
across the codebase.
"""

from dotenv import load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict

# pydantic-settings' env_file= only populates the Settings model below —
# it does NOT inject values into the real process os.environ. Third-party
# SDKs (langsmith, anthropic) read tracing config via os.getenv() directly,
# so without this explicit load_dotenv() call, they never see .env values
# at all — this is why LangSmith tracing silently did nothing even after
# wrapping the client.
load_dotenv()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # --- Anthropic / Voyage ---
    anthropic_api_key: str
    voyage_api_key: str

    # --- LangSmith ---
    langchain_tracing_v2: bool = True
    langchain_api_key: str = ""
    langchain_project: str = "flatrock-po-agent"

    # --- Internal auth ---
    api_key: str

    # --- Storage paths ---
    database_path: str = "./data/purchase_orders.db"
    chroma_persist_path: str = "./data/chroma_db"

    # --- Gate thresholds (kept out of code, per ADR-003) ---
    gate1_completeness_threshold: float = 0.6
    gate1_max_retries: int = 1
    gate2_validation_rate_threshold: float = 0.75


# Single shared instance — import this, don't instantiate Settings() elsewhere.
settings = Settings()