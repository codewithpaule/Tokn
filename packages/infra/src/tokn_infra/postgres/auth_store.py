"""PostgreSQL implementation of ``AuthStore``.

Durability lives here: accounts, sessions, one-time tokens and recovery codes. Login
throttle counters are delegated to a ``ThrottleStore`` because they are allowed to be
lost; Redis holds them in production.

Every mutation that must happen once — consuming a token, burning a recovery code — is a
conditional UPDATE with RETURNING, so two concurrent requests cannot both succeed.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from tokn_core.auth.store import AccountRecord, SessionRecord, ThrottleStore
from tokn_core.auth.throttle import ThrottleState
from tokn_core.auth.tokens import TokenKind
from tokn_core.errors import AccountExistsError
from tokn_infra.db.models import Account, AuthSession, AuthToken, EmailOutbox, RecoveryCode
from tokn_infra.postgres.session import SessionFactory


class PostgresAuthStore:
    def __init__(self, sessions: SessionFactory, throttle: ThrottleStore) -> None:
        self._sessions = sessions
        self._throttle = throttle

    # -- accounts ---------------------------------------------------------

    async def create_account(self, email: str, password_hash: str) -> AccountRecord:
        async with self._sessions() as session, session.begin():
            account = Account(email=email.strip().lower(), password_hash=password_hash)
            session.add(account)
            try:
                await session.flush()
            except IntegrityError as error:
                raise AccountExistsError(email) from error
            return _to_record(account, ())

    async def account_by_email(self, email: str) -> AccountRecord | None:
        async with self._sessions() as session:
            account = await session.scalar(
                select(Account).where(Account.email == email.strip().lower())
            )
            if account is None:
                return None
            return _to_record(account, await _recovery_digests(session, account.id))

    async def account_by_id(self, account_id: str) -> AccountRecord | None:
        async with self._sessions() as session:
            account = await session.get(Account, _uuid(account_id))
            if account is None:
                return None
            return _to_record(account, await _recovery_digests(session, account.id))

    async def set_email_verified(self, account_id: str, now: datetime) -> None:
        async with self._sessions() as session, session.begin():
            await session.execute(
                update(Account)
                .where(Account.id == _uuid(account_id))
                .values(email_verified_at=now, status="active")
            )

    async def set_password_hash(self, account_id: str, password_hash: str) -> None:
        async with self._sessions() as session, session.begin():
            await session.execute(
                update(Account)
                .where(Account.id == _uuid(account_id))
                .values(password_hash=password_hash)
            )

    async def set_totp_secret(self, account_id: str, secret: str) -> None:
        async with self._sessions() as session, session.begin():
            await session.execute(
                update(Account)
                .where(Account.id == _uuid(account_id))
                .values(totp_secret_enc=secret.encode("utf-8"))
            )

    async def set_recovery_codes(self, account_id: str, digests: tuple[str, ...]) -> None:
        async with self._sessions() as session, session.begin():
            await session.execute(
                delete(RecoveryCode).where(RecoveryCode.account_id == _uuid(account_id))
            )
            session.add_all(
                RecoveryCode(account_id=_uuid(account_id), digest=digest) for digest in digests
            )

    async def consume_recovery_code(self, account_id: str, digest: str) -> bool:
        async with self._sessions() as session, session.begin():
            result = await session.execute(
                update(RecoveryCode)
                .where(
                    RecoveryCode.account_id == _uuid(account_id),
                    RecoveryCode.digest == digest,
                    RecoveryCode.used_at.is_(None),
                )
                .values(used_at=func.now())
                .returning(RecoveryCode.id)
            )
            return result.scalar_one_or_none() is not None

    # -- tokens -----------------------------------------------------------

    async def issue_token(
        self, account_id: str, digest: str, kind: TokenKind, expires_at: datetime
    ) -> None:
        async with self._sessions() as session, session.begin():
            session.add(
                AuthToken(
                    account_id=_uuid(account_id),
                    digest=digest,
                    kind=kind.value,
                    expires_at=expires_at,
                )
            )

    async def consume_token(self, digest: str, kind: TokenKind, now: datetime) -> str | None:
        async with self._sessions() as session, session.begin():
            result = await session.execute(
                update(AuthToken)
                .where(
                    AuthToken.digest == digest,
                    AuthToken.kind == kind.value,
                    AuthToken.used_at.is_(None),
                    AuthToken.expires_at > now,
                )
                .values(used_at=now)
                .returning(AuthToken.account_id)
            )
            account_id = result.scalar_one_or_none()
            return None if account_id is None else str(account_id)

    # -- sessions ---------------------------------------------------------

    async def create_session(
        self,
        digest: str,
        account_id: str,
        now: datetime,
        ip: str | None,
        user_agent: str | None,
    ) -> None:
        async with self._sessions() as session, session.begin():
            session.add(
                AuthSession(
                    digest=digest,
                    account_id=_uuid(account_id),
                    created_at=now,
                    last_seen_at=now,
                    ip=ip,
                    user_agent=user_agent,
                )
            )

    async def session(self, digest: str) -> SessionRecord | None:
        async with self._sessions() as session:
            row = await session.scalar(select(AuthSession).where(AuthSession.digest == digest))
            if row is None:
                return None
            return SessionRecord(
                digest=row.digest,
                account_id=str(row.account_id),
                created_at=row.created_at,
                last_seen_at=row.last_seen_at,
            )

    async def touch_session(self, digest: str, now: datetime) -> None:
        async with self._sessions() as session, session.begin():
            await session.execute(
                update(AuthSession).where(AuthSession.digest == digest).values(last_seen_at=now)
            )

    async def delete_session(self, digest: str) -> None:
        async with self._sessions() as session, session.begin():
            await session.execute(delete(AuthSession).where(AuthSession.digest == digest))

    async def delete_sessions(self, account_id: str, *, keep: str | None = None) -> None:
        statement = delete(AuthSession).where(AuthSession.account_id == _uuid(account_id))
        if keep is not None:
            statement = statement.where(AuthSession.digest != keep)
        async with self._sessions() as session, session.begin():
            await session.execute(statement)

    # -- throttle and outbox ---------------------------------------------

    async def throttle(self, identity: str) -> ThrottleState:
        return await self._throttle.throttle(identity)

    async def save_throttle(self, identity: str, state: ThrottleState) -> None:
        await self._throttle.save_throttle(identity, state)

    async def enqueue_email(self, to: str, subject: str, body: str) -> None:
        """Queue mail in the caller's transaction; a worker sends it later."""
        async with self._sessions() as session, session.begin():
            session.add(EmailOutbox(to_address=to, subject=subject, body=body))


def _to_record(account: Account, recovery_digests: tuple[str, ...]) -> AccountRecord:
    return AccountRecord(
        id=str(account.id),
        email=account.email,
        password_hash=account.password_hash,
        email_verified=account.email_verified_at is not None,
        totp_secret=(
            None if account.totp_secret_enc is None else account.totp_secret_enc.decode("utf-8")
        ),
        status=account.status,
        recovery_code_digests=recovery_digests,
    )


async def _recovery_digests(session: AsyncSession, account_id: uuid.UUID) -> tuple[str, ...]:
    rows = await session.scalars(
        select(RecoveryCode.digest).where(
            RecoveryCode.account_id == account_id, RecoveryCode.used_at.is_(None)
        )
    )
    return tuple(rows)


def _uuid(value: str) -> uuid.UUID:
    """Ids arrive as strings from the domain; PostgreSQL wants UUID objects."""
    try:
        return uuid.UUID(value)
    except ValueError as error:
        raise ValueError(f"not a UUID: {value}") from error
