"""The receipt.

Metadata only: which model, which provider, how many tokens, what it cost, how long it
took, how it ended. Never the prompt, never the completion.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from tokn_core.money import Naira
from tokn_core.pricing import TokenUsage


class Outcome(StrEnum):
    OK = "ok"
    STREAM_ERROR = "stream_error"
    UPSTREAM_ERROR = "upstream_error"
    CLIENT_DISCONNECT = "client_disconnect"
    USAGE_MISSING = "usage_missing"


@dataclass(frozen=True, slots=True)
class UsageEvent:
    request_id: str
    account_id: str
    key_id: str
    model_alias: str
    upstream_model: str
    provider: str
    usage: TokenUsage
    upstream_cost: Naira
    charged: Naira
    latency_ms: int
    outcome: Outcome
    flagged_for_review: bool
    created_at: datetime
