"""Gateway configuration. Twelve-factor: everything comes from the environment."""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class GatewaySettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="TOKN_", extra="ignore")

    env: str = "development"

    # Required: a missing database URL fails at startup, not at the first request.
    database_url: str
    key_pepper: str
    key_prefix: str = "tokn"

    openai_api_key: str | None = None
    anthropic_api_key: str | None = None
    openai_base_url: str = "https://api.openai.com/v1"
    anthropic_base_url: str = "https://api.anthropic.com/v1"

    burst: int = 40
    requests_per_second: int = 20
    account_burst: int = 120
    account_requests_per_second: int = 60
    database_pool_size: int = 5

    breaker_threshold: int = 5
    breaker_reset_seconds: int = 30

    def validated(self) -> GatewaySettings:
        if len(self.key_pepper) < 16:
            raise ValueError("TOKN_KEY_PEPPER must be at least 16 characters")
        if self.env != "development" and "change-me" in self.key_pepper:
            raise ValueError("TOKN_KEY_PEPPER is still the development placeholder")
        if min(self.burst, self.requests_per_second, self.account_burst) < 1:
            raise ValueError("rate limits must be positive")
        return self
