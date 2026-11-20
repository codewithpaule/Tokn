"""PostgreSQL implementation of ``KeyStore``.

The spend total is a column on ``api_keys``, incremented in the same transaction that
settles a request, so a cap check never has to scan the ledger.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import select, update

from tokn_core.api_keys import KeyRecord
from tokn_infra.db.models import ApiKey
from tokn_infra.postgres.session import SessionFactory


class PostgresKeyStore:
    def __init__(self, sessions: SessionFactory) -> None:
        self._sessions = sessions

    async def by_prefix(self, prefix: str) -> KeyRecord | None:
        async with self._sessions() as session:
            row = await session.scalar(select(ApiKey).where(ApiKey.prefix == prefix))
            return None if row is None else _to_record(row)

    async def by_id(self, key_id: str) -> KeyRecord | None:
        async with self._sessions() as session:
            row = await session.get(ApiKey, _uuid(key_id))
            return None if row is None else _to_record(row)

    async def list_for_account(self, account_id: str) -> tuple[KeyRecord, ...]:
        async with self._sessions() as session:
            rows = await session.scalars(
                select(ApiKey)
                .where(ApiKey.account_id == _uuid(account_id))
                .order_by(ApiKey.created_at.desc())
            )
            return tuple(_to_record(row) for row in rows)

    async def create(self, record: KeyRecord) -> None:
        async with self._sessions() as session, session.begin():
            session.add(
                ApiKey(
                    id=_uuid(record.id),
                    account_id=_uuid(record.account_id),
                    prefix=record.prefix,
                    key_hash=bytes.fromhex(record.digest),
                    name=record.name or record.prefix,
                    scopes=sorted(record.scopes),
                    spend_cap_micro=record.spend_cap_micro,
                    expires_at=record.expires_at,
                    ip_allowlist=(
                        None if record.ip_allowlist is None else list(record.ip_allowlist)
                    ),
                    concurrency_limit=record.concurrency_limit,
                )
            )

    async def touch_last_used(self, key_id: str, now: datetime) -> None:
        async with self._sessions() as session, session.begin():
            await session.execute(
                update(ApiKey).where(ApiKey.id == _uuid(key_id)).values(last_used_at=now)
            )

    async def record_spend(self, key_id: str, amount_micro: int) -> None:
        if amount_micro < 0:
            raise ValueError("a spend increment cannot be negative")
        async with self._sessions() as session, session.begin():
            await session.execute(
                update(ApiKey)
                .where(ApiKey.id == _uuid(key_id))
                .values(spent_micro=ApiKey.spent_micro + amount_micro)
            )

    async def revoke(self, key_id: str, now: datetime) -> None:
        async with self._sessions() as session, session.begin():
            await session.execute(
                update(ApiKey).where(ApiKey.id == _uuid(key_id)).values(revoked_at=now)
            )


def _to_record(row: ApiKey) -> KeyRecord:
    return KeyRecord(
        id=str(row.id),
        account_id=str(row.account_id),
        prefix=row.prefix,
        digest=bytes(row.key_hash).hex(),
        scopes=frozenset(row.scopes),
        spent_micro=row.spent_micro,
        spend_cap_micro=row.spend_cap_micro,
        expires_at=row.expires_at,
        revoked_at=row.revoked_at,
        ip_allowlist=None if row.ip_allowlist is None else tuple(row.ip_allowlist),
        auto_paused=row.auto_paused,
        concurrency_limit=row.concurrency_limit,
        name=row.name,
        last_used_at=row.last_used_at,
    )


def _uuid(value: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except ValueError as error:
        raise ValueError(f"not a UUID: {value}") from error
