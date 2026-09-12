from dataclasses import dataclass
from functools import lru_cache
from typing import Literal
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from pydantic_settings import BaseSettings, SettingsConfigDict

_SSL_QUERY_KEYS = {"sslmode", "channel_binding"}


@dataclass(frozen=True)
class NormalizedDatabaseUrl:
    """asyncpg does not understand `sslmode`/`channel_binding` query params
    (used by libpq-based URLs, e.g. Neon's default connection string), so we
    strip them from the URL and surface an explicit `ssl` flag instead."""

    url: str
    ssl: bool


def normalize_database_url(url: str) -> NormalizedDatabaseUrl:
    parts = urlsplit(url)
    query_pairs = parse_qsl(parts.query, keep_blank_values=True)

    ssl_required = any(
        key == "sslmode" and value.lower() in {"require", "verify-ca", "verify-full"}
        for key, value in query_pairs
    )
    remaining_pairs = [(key, value) for key, value in query_pairs if key not in _SSL_QUERY_KEYS]

    cleaned = urlunsplit(
        (parts.scheme, parts.netloc, parts.path, urlencode(remaining_pairs), parts.fragment)
    )
    return NormalizedDatabaseUrl(url=cleaned, ssl=ssl_required)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str
    gemini_api_key: str = ""
    gemini_model: str = "gemini-3.6-flash"
    ai_daily_call_budget: int = 200
    ai_failure_threshold: int = 3
    ai_cooldown_minutes: int = 10
    # Gemini free-tier daily quota (20 requests/day) keeps getting exhausted by
    # auto-classification on every ingest, leaving no budget for manual digest
    # generation. Flip back to True once the quota situation is sorted.
    ai_auto_classify_enabled: bool = False
    refresh_token: str
    app_env: Literal["local", "prod"] = "local"
    log_level: str = "INFO"

    @property
    def normalized_database_url(self) -> NormalizedDatabaseUrl:
        return normalize_database_url(self.database_url)


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
