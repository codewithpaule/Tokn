"""Console configuration. Twelve-factor: everything comes from the environment."""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Refuses to start when a secret is missing or too weak."""

    model_config = SettingsConfigDict(env_prefix="TOKN_", extra="ignore")

    env: str = "development"
    base_url: str = "http://localhost:8080"
    console_url: str = "http://localhost:8081"
    support_email: str = "support@tokn.ng"
    product_name: str = "Tokn"
    key_prefix: str = "tokn"

    session_secret: str = Field(default="dev-only-session-secret-change-me")
    key_pepper: str = Field(default="dev-only-pepper-change-me-please")
    secure_cookies: bool = True
    enforce_https: bool = False

    def validated(self) -> Settings:
        """Fail fast rather than serve auth with a placeholder secret in production."""
        if self.env != "development":
            for name in ("session_secret", "key_pepper"):
                value: str = getattr(self, name)
                if "change-me" in value or len(value) < 32:
                    raise ValueError(f"{name.upper()} must be a real secret in {self.env}")
        if len(self.key_pepper) < 16:
            raise ValueError("KEY_PEPPER must be at least 16 characters")
        return self
