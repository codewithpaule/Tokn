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
    support_email: str = "support@brewkeg.dev"
    product_name: str = "Brewkeg"
    key_prefix: str = "brewkeg"
    key_environment: str = "live"
    domain: str = "brewkeg.dev"
    cli_available: bool = False

    session_secret: str = Field(default="dev-only-session-secret-change-me")
    key_pepper: str = Field(default="dev-only-pepper-change-me-please")
    secure_cookies: bool = True
    enforce_https: bool = False
    admin_emails: str = ""

    # When set, the console runs on PostgreSQL instead of the in-memory store.
    database_url: str | None = None
    database_pool_size: int = 5

    @property
    def cookie_secure(self) -> bool:
        """``__Host-`` cookies need TLS. Development on localhost is plain HTTP."""
        return self.secure_cookies and self.env != "development"

    @property
    def administrators(self) -> frozenset[str]:
        return frozenset(
            part.strip().lower() for part in self.admin_emails.split(",") if part.strip()
        )

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
