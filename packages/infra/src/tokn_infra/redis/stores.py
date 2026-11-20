"""Redis adapters for the two things we are allowed to lose: the key cache, login throttle
counters and rate-limit buckets.

If Redis is empty, the worst outcome is one extra database read per request and a burst of
traffic. Money never lives here.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta

from redis.asyncio import Redis

from tokn_core.api_keys import KeyRecord
from tokn_core.auth.throttle import ThrottleState
from tokn_core.ratelimit import LimitPolicy

# A faithful mirror of tokn_core.ratelimit.TokenBucket, evaluated atomically in Redis.
# Tokens are counted in micro-tokens; time is passed in as microseconds.
BUCKET_SCRIPT = """
local key = KEYS[1]
local now = tonumber(ARGV[1])
local capacity = tonumber(ARGV[2])
local refill = tonumber(ARGV[3])
local cost = tonumber(ARGV[4])
local ttl_ms = tonumber(ARGV[5])
local data = redis.call('HMGET', key, 'tokens', 'ts')
local tokens = tonumber(data[1])
local ts = tonumber(data[2])
if tokens == nil then
  tokens = capacity * 1000000
  ts = now
end
local elapsed = now - ts
if elapsed < 0 then elapsed = 0 end
tokens = math.min(capacity * 1000000, tokens + elapsed * refill)
local needed = cost * 1000000
local allowed = 0
if tokens >= needed then
  tokens = tokens - needed
  allowed = 1
end
redis.call('HSET', key, 'tokens', tokens, 'ts', now)
redis.call('PEXPIRE', key, ttl_ms)
return {allowed, tokens}
"""


def _serialise(record: KeyRecord) -> str:
    return json.dumps(
        {
            "id": record.id,
            "account_id": record.account_id,
            "prefix": record.prefix,
            "digest": record.digest,
            "scopes": sorted(record.scopes),
            "spent_micro": record.spent_micro,
            "spend_cap_micro": record.spend_cap_micro,
            "expires_at": None if record.expires_at is None else record.expires_at.isoformat(),
            "revoked_at": None if record.revoked_at is None else record.revoked_at.isoformat(),
            "ip_allowlist": None if record.ip_allowlist is None else list(record.ip_allowlist),
            "auto_paused": record.auto_paused,
            "concurrency_limit": record.concurrency_limit,
            "name": record.name,
        }
    )


def _deserialise(payload: str) -> KeyRecord:
    data = json.loads(payload)
    return KeyRecord(
        id=data["id"],
        account_id=data["account_id"],
        prefix=data["prefix"],
        digest=data["digest"],
        scopes=frozenset(data["scopes"]),
        spent_micro=data["spent_micro"],
        spend_cap_micro=data["spend_cap_micro"],
        expires_at=_maybe_time(data.get("expires_at")),
        revoked_at=_maybe_time(data.get("revoked_at")),
        ip_allowlist=None if data["ip_allowlist"] is None else tuple(data["ip_allowlist"]),
        auto_paused=data["auto_paused"],
        concurrency_limit=data["concurrency_limit"],
        name=data.get("name", ""),
    )


def _maybe_time(value: str | None) -> datetime | None:
    return None if value is None else datetime.fromisoformat(value)


class RedisKeyCache:
    def __init__(self, client: Redis) -> None:
        self._client = client

    @staticmethod
    def _name(prefix: str) -> str:
        return f"tokn:key:{prefix}"

    async def get(self, prefix: str) -> KeyRecord | None:
        payload = await self._client.get(self._name(prefix))
        if payload is None:
            return None
        return _deserialise(payload if isinstance(payload, str) else payload.decode())

    async def put(self, record: KeyRecord, ttl_seconds: int) -> None:
        await self._client.set(self._name(record.prefix), _serialise(record), ex=ttl_seconds)

    async def invalidate(self, prefix: str) -> None:
        await self._client.delete(self._name(prefix))


class RedisThrottleStore:
    def __init__(self, client: Redis) -> None:
        self._client = client

    @staticmethod
    def _name(identity: str) -> str:
        return f"tokn:throttle:{identity}"

    async def throttle(self, identity: str) -> ThrottleState:
        values = await self._client.hmget(self._name(identity), "failures", "last")
        failures, last = values[0], values[1]
        if not failures:
            return ThrottleState()
        return ThrottleState(
            failures=int(failures),
            last_failure_at=None if last is None else datetime.fromisoformat(_text(last)),
        )

    async def save_throttle(self, identity: str, state: ThrottleState) -> None:
        name = self._name(identity)
        await self._client.hset(
            name,
            mapping={
                "failures": state.failures,
                "last": "" if state.last_failure_at is None else state.last_failure_at.isoformat(),
            },
        )
        await self._client.expire(name, int(timedelta(hours=2).total_seconds()))


class RedisRateLimiter:
    def __init__(self, client: Redis) -> None:
        self._client = client
        self._script = client.register_script(BUCKET_SCRIPT)

    async def consume(
        self, key: str, *, policy: LimitPolicy, now: datetime, cost: int = 1
    ) -> tuple[bool, int]:
        allowed, tokens = await self._script(
            keys=[f"tokn:rate:{key}"],
            args=[
                int(now.timestamp() * 1_000_000),
                policy.capacity,
                policy.refill_per_second,
                cost,
                120_000,
            ],
        )
        if allowed:
            return True, 0
        if policy.refill_per_second == 0:
            return False, 60
        missing = policy.capacity * 1_000_000 - int(tokens)
        return False, max(1, -(-missing // (policy.refill_per_second * 1_000_000)))

    async def acquire(self, key: str, *, limit: int) -> bool:
        name = f"tokn:concurrency:{key}"
        current = await self._client.incr(name)
        await self._client.expire(name, 120)
        if current > limit:
            await self._client.decr(name)
            return False
        return True

    async def release(self, key: str) -> None:
        name = f"tokn:concurrency:{key}"
        if await self._client.decr(name) < 0:
            await self._client.set(name, 0)


def _text(value: bytes | str) -> str:
    return value if isinstance(value, str) else value.decode()
