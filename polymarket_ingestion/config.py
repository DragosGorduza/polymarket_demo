from dataclasses import dataclass
import os


@dataclass(frozen=True)
class Settings:
    gamma_base_url: str = os.getenv("GAMMA_BASE_URL", "https://gamma-api.polymarket.com")
    clob_base_url: str = os.getenv("CLOB_BASE_URL", "https://clob.polymarket.com")
    data_base_url: str = os.getenv("DATA_BASE_URL", "https://data-api.polymarket.com")
    request_timeout_seconds: int = int(os.getenv("REQUEST_TIMEOUT_SECONDS", "20"))
    user_agent: str = os.getenv("USER_AGENT", "polymarket-ingestion-mvp/0.1")
    clob_api_key: str = os.getenv("CLOB_API_KEY", "")
    data_api_key: str = os.getenv("DATA_API_KEY", "")
