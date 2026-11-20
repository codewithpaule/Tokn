"""In-memory throttle counters.

Login backoff belongs in Redis in production, where every instance shares it. Until Redis
is attached this keeps the behaviour correct for a single instance and loses its memory on
restart — which, for a throttle, is the cheapest possible failure.
"""

from __future__ import annotations

from tokn_core.auth.throttle import ThrottleState


class InMemoryThrottleStore:
    def __init__(self) -> None:
        self._states: dict[str, ThrottleState] = {}

    async def throttle(self, identity: str) -> ThrottleState:
        return self._states.get(identity, ThrottleState())

    async def save_throttle(self, identity: str, state: ThrottleState) -> None:
        self._states[identity] = state
