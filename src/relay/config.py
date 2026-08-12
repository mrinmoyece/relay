"""Runtime configuration via environment variables (12-factor).

Everything has a safe local default: with zero configuration Relay runs
with the in-memory store and the deterministic mock provider, so the full
system (API, engine, tests, demo) works on any laptop with no secrets.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="RELAY_", env_file=".env", extra="ignore")

    # Storage. None -> in-memory store (dev/tests). Set to a postgres DSN
    # (postgresql://user:pass@host:5432/relay) for durable production mode.
    database_url: str | None = None

    # LLM provider: "mock" (deterministic, no key needed) or "anthropic".
    provider: str = "mock"
    model: str = "claude-sonnet-4-5"
    anthropic_api_key: str | None = None

    # Observability. None -> tracing is a structured-log no-op.
    otel_endpoint: str | None = None
    service_name: str = "relay"

    # Engine tuning
    tool_timeout_seconds: float = Field(default=30.0, gt=0)
    tool_max_attempts: int = Field(default=3, ge=1, le=10)
    llm_timeout_seconds: float = Field(default=120.0, gt=0)
    llm_max_attempts: int = Field(default=3, ge=1, le=10)
    llm_retry_backoff_seconds: float = Field(default=0.25, ge=0, le=60)

    @property
    def durable(self) -> bool:
        return self.database_url is not None


@lru_cache
def get_settings() -> Settings:
    return Settings()
