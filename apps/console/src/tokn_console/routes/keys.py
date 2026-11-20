"""API key listing, creation and revocation.

Split out of the single routes module in UI-1. Behaviour is unchanged; every dependency
is reached through ``console`` rather than through a closure.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, Form, HTTPException, Request, Response
from fastapi.responses import RedirectResponse

from tokn_console.web import require_csrf
from tokn_core.api_keys import KeyRecord
from tokn_core.auth.store import AccountRecord
from tokn_core.auth.totp import verify
from tokn_core.key_auth import (
    KeyIssuanceRefusedError,
    NewKeyRequest,
    issue_key_with_retry,
    list_keys,
    revoke_key,
)
from tokn_core.money import Naira

if TYPE_CHECKING:
    from tokn_console.context import Console


def build_router(console: Console) -> APIRouter:
    router = APIRouter()

    # ------------------------------------------------------------------ keys

    def keys_view(
        request: Request,
        account: AccountRecord,
        keys: tuple[KeyRecord, ...],
        *,
        error: str | None = None,
        status_code: int = 200,
    ) -> Response:
        return console.render(
            request,
            "keys.html",
            status_code=status_code,
            account=account,
            keys=keys,
            enrolled=account.totp_secret is not None,
            error=error,
        )

    def step_up_ok(account: AccountRecord, code: str) -> bool:
        return account.totp_secret is not None and verify(
            account.totp_secret, code, console.clock.now()
        )

    @router.get("/app/keys")
    async def keys_page(request: Request) -> Response:
        account = await console.current_account(request)
        if account is None:
            return RedirectResponse("/login", status_code=303)
        return keys_view(request, account, await list_keys(console.key_store, account.id))

    @router.post("/app/keys", dependencies=[Depends(require_csrf)])
    async def create_key(
        request: Request,
        name: str = Form(""),
        code: str = Form(""),
        spend_cap_naira: str = Form(""),
    ) -> Response:
        account = await console.current_account(request)
        if account is None:
            return RedirectResponse("/login", status_code=303)
        if account.totp_secret is None:
            return RedirectResponse("/app/2fa", status_code=303)

        keys = await list_keys(console.key_store, account.id)
        if not step_up_ok(account, code):
            return keys_view(
                request, account, keys, error="That code did not match.", status_code=422
            )

        try:
            cap = _parse_cap(spend_cap_naira)
        except (ValueError, ArithmeticError):
            return keys_view(
                request,
                account,
                keys,
                error="Enter the cap in naira, for example 5000.",
                status_code=422,
            )

        try:
            key = await issue_key_with_retry(
                request=NewKeyRequest(account_id=account.id, name=name, spend_cap_micro=cap),
                store=console.key_store,
                pepper=console.settings.key_pepper,
                product=console.settings.key_prefix,
                environment=console.settings.key_environment,
                new_id=uuid.uuid4().hex,
            )
        except KeyIssuanceRefusedError as error:
            return keys_view(request, account, keys, error=str(error), status_code=422)

        return console.render(request, "key_created.html", raw=key.raw, prefix=key.prefix)

    @router.post("/app/keys/revoke", dependencies=[Depends(require_csrf)])
    async def revoke(request: Request, key_id: str = Form(""), code: str = Form("")) -> Response:
        account = await console.current_account(request)
        if account is None:
            return RedirectResponse("/login", status_code=303)

        record = await console.key_store.by_id(key_id)
        if record is None or record.account_id != account.id:
            raise HTTPException(status_code=404, detail="no such key")

        keys = await list_keys(console.key_store, account.id)
        if not step_up_ok(account, code):
            return keys_view(
                request, account, keys, error="That code did not match.", status_code=422
            )

        await revoke_key(
            prefix=record.prefix,
            key_id=record.id,
            store=console.key_store,
            cache=console.key_cache,
            clock=console.clock,
        )
        return RedirectResponse("/app/keys", status_code=303)

    return router


def _parse_cap(value: str) -> int | None:
    """Parse a naira amount into micro-naira. Empty means no cap."""
    text = value.strip().replace(",", "")
    if not text:
        return None
    return Naira.from_naira(text).micro
