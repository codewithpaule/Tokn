"""Reading usage out of a provider's event stream.

Usage arrives differently from each protocol. OpenAI sends one final chunk with totals
when ``stream_options.include_usage`` is set. Anthropic splits it: input tokens appear in
``message_start``, output tokens accumulate in ``message_delta``.

Malformed events are ignored rather than raised: a provider's stream must never crash the
gateway, and a missing total is handled by settlement, not by an exception here.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from tokn_core.pricing import TokenUsage

DONE = b"[DONE]"


def sse_payload(line: bytes) -> bytes | None:
    """Return the JSON body of an SSE ``data:`` line, or None for anything else."""
    stripped = line.strip()
    if not stripped.startswith(b"data:"):
        return None
    body = stripped[len(b"data:") :].strip()
    if not body or body == DONE:
        return None
    return body


def _int(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


@dataclass(slots=True)
class SseSplitter:
    """Turns an arbitrary byte stream into complete lines.

    A network chunk is not a line: it can hold half an event, or three of them. Parsing
    per chunk silently loses usage, which is exactly the bug this class exists to prevent.
    """

    _buffer: bytes = field(default=b"")

    def feed(self, chunk: bytes) -> list[bytes]:
        data = self._buffer + chunk
        parts = data.split(b"\n")
        self._buffer = parts.pop()
        return parts

    def flush(self) -> list[bytes]:
        remainder, self._buffer = self._buffer, b""
        return [remainder] if remainder else []


@dataclass(slots=True)
class UsageAccumulator:
    """Accumulates one request's usage across however many events it takes."""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    saw_usage: bool = False
    _seen_chunks: int = field(default=0)

    def feed_openai(self, payload: dict[str, object]) -> None:
        usage = payload.get("usage")
        if not isinstance(usage, dict):
            return
        details = usage.get("prompt_tokens_details")
        cached = _int(details.get("cached_tokens")) if isinstance(details, dict) else 0
        self.input_tokens = _int(usage.get("prompt_tokens"))
        self.output_tokens = _int(usage.get("completion_tokens"))
        self.cache_read_tokens = cached
        self.saw_usage = True

    def feed_anthropic(self, payload: dict[str, object]) -> None:
        kind = payload.get("type")
        if kind == "message_start":
            message = payload.get("message")
            usage = message.get("usage") if isinstance(message, dict) else None
            if isinstance(usage, dict):
                self.input_tokens = _int(usage.get("input_tokens"))
                self.cache_read_tokens = _int(usage.get("cache_read_input_tokens"))
                self.cache_write_tokens = _int(usage.get("cache_creation_input_tokens"))
                self.saw_usage = True
        elif kind == "message_delta":
            usage = payload.get("usage")
            if isinstance(usage, dict):
                self.output_tokens = _int(usage.get("output_tokens"))
                self.saw_usage = True

    def feed(self, provider: str, line: bytes) -> None:
        body = sse_payload(line)
        if body is None:
            return
        try:
            payload = json.loads(body)
        except ValueError:
            return
        if not isinstance(payload, dict):
            return
        self._seen_chunks += 1
        if provider == "anthropic":
            self.feed_anthropic(payload)
        else:
            self.feed_openai(payload)

    def usage(self) -> TokenUsage:
        return TokenUsage(
            input_tokens=self.input_tokens,
            output_tokens=self.output_tokens,
            cache_read_tokens=self.cache_read_tokens,
            cache_write_tokens=self.cache_write_tokens,
        )

    @property
    def chunks_seen(self) -> int:
        return self._seen_chunks
