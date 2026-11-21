"""Request body limits, as pure ASGI middleware.

Two checks, because a body can lie about its size:

* a declared ``Content-Length`` larger than the cap is refused before a byte is read;
* the bytes actually received are counted, which catches a chunked body that declares
  nothing at all.

If the limit is crossed after the response has started we cannot replace it with a 413,
so the request is abandoned instead — the upstream sees a closed connection rather than a
truncated-but-accepted body.
"""

from __future__ import annotations

import json

from starlette.types import ASGIApp, Message, Receive, Scope, Send

MAX_BODY_BYTES = 20 * 1024 * 1024


class _BodyTooLarge(Exception):
    """Raised from the wrapped receive to unwind the application."""


class BodyLimitMiddleware:
    def __init__(self, app: ASGIApp, *, max_bytes: int = MAX_BODY_BYTES) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        declared = _declared_length(scope)
        if declared is not None and declared > self.max_bytes:
            await _send_too_large(send)
            return

        received = 0
        response_started = False

        async def counting_send(message: Message) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        async def counting_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    raise _BodyTooLarge
            return message

        try:
            await self.app(scope, counting_receive, counting_send)
        except _BodyTooLarge:
            if not response_started:
                await _send_too_large(send)


def _declared_length(scope: Scope) -> int | None:
    for name, value in scope["headers"]:
        if name == b"content-length":
            try:
                return int(value)
            except ValueError:
                return None
    return None


async def _send_too_large(send: Send) -> None:
    body = json.dumps({"error": "request_too_large"}).encode()
    await send(
        {
            "type": "http.response.start",
            "status": 413,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})
