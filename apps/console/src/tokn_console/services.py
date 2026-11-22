"""Camera-ready wiring for the console.

One object holds every adapter the console screens touch, so a route never constructs a
database session or guesses which store it is talking to.
"""

from __future__ import annotations

from dataclasses import dataclass

from tokn_console.config import Settings
from tokn_core.auth.ports import Clock, PasswordHasher
from tokn_core.auth.store import AuthStore
from tokn_core.dashboard import DashboardStore
from tokn_core.key_auth import KeyStore
from tokn_infra.clock import SystemClock
from tokn_infra.memory.auth_store import InMemoryAuthStore
from tokn_infra.memory.dashboard_store import InMemoryDashboardStore
from tokn_infra.memory.key_store import InMemoryKeyStore
from tokn_infra.memory.throttle import InMemoryThrottleStore
from tokn_infra.security.argon2_hasher import Argon2PasswordHasher
from tokn_infra.site_store import InMemorySiteSettings, PostgresSiteSettings, SiteSettingsStore


@dataclass(slots=True)
class Services:
    settings: Settings
    auth: AuthStore
    keys: KeyStore
    dashboard: DashboardStore
    site: SiteSettingsStore
    hasher: PasswordHasher
    clock: Clock

    @property
    def secure_cookies(self) -> bool:
        return self.settings.cookie_secure

    @property
    def session_cookie(self) -> str:
        return "__Host-session" if self.secure_cookies else "tokn-session"

    @property
    def csrf_cookie(self) -> str:
        return "__Host-csrf" if self.secure_cookies else "tokn-csrf"


def build_services(settings: Settings) -> Services:
    clock = SystemClock()
    hasher = Argon2PasswordHasher()
    if settings.database_url:
        from tokn_infra.postgres import (
            PostgresAuthStore,
            PostgresDashboardStore,
            PostgresKeyStore,
            create_engine,
            create_session_factory,
        )

        engine = create_engine(settings.database_url, pool_size=settings.database_pool_size)
        sessions = create_session_factory(engine)
        return Services(
            settings=settings,
            auth=PostgresAuthStore(sessions, InMemoryThrottleStore()),
            keys=PostgresKeyStore(sessions),
            dashboard=PostgresDashboardStore(sessions),
            site=PostgresSiteSettings(sessions),
            hasher=hasher,
            clock=clock,
        )

    return Services(
        settings=settings,
        auth=InMemoryAuthStore(),
        keys=InMemoryKeyStore(),
        dashboard=InMemoryDashboardStore(),
        site=InMemorySiteSettings(),
        hasher=hasher,
        clock=clock,
    )
