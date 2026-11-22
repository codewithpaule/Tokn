"""Signed-in screens and the JSON endpoints behind them.

Everything here is owner-scoped: a key, a ledger line and a usage row are only returned
when the session account owns them. The dashboard never writes money; the only mutations
are API keys, feedback, password, sessions and the operator's site settings.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse

from tokn_console import accounts, ui
from tokn_console.responses import error_response, finish, redirect, token_for, wants_json
from tokn_console.services import Services
from tokn_console.web import submitted_csrf
from tokn_core.api_keys import KeyRecord
from tokn_core.auth.store import AccountRecord
from tokn_core.dashboard import LedgerLine, UsageLine
from tokn_core.key_auth import NewKeyRequest, issue_key_with_retry
from tokn_core.site import FONTS, SiteSettings

PLANS = (
    ("Go", 7_000, "Light usage every month"),
    ("Pro", 16_000, "Standard usage every month"),
    ("Pro 2x", 27_500, "Expanded usage every month"),
    ("Pro Max", 132_500, "Maximum usage every month"),
)


def _key_rows(keys: tuple[KeyRecord, ...], now: datetime, csrf: str) -> str:
    if not keys:
        return (
            '<div class="empty">No keys yet. Create one to send your first request. '
            '<a href="/dashboard/keys">Create a key</a>.</div>'
        )
    rows = []
    for key in keys:
        if key.revoked_at is not None:
            state = '<span class="badge red">revoked</span>'
        elif key.is_usable(now):
            state = '<span class="badge green">active</span>'
        else:
            state = '<span class="badge">paused</span>'
        action = (
            f'<form method="post" action="/api/keys/{ui.e(key.id)}/revoke" class="inline-form">'
            f"{ui.csrf_field(csrf)}"
            '<button class="btn quiet small" type="submit" data-confirm="Revoke this key? '
            'Requests using it will fail immediately.">Revoke</button></form>'
            if key.revoked_at is None
            else ""
        )
        rows.append(
            f"<tr><td><strong>{ui.e(key.name or key.prefix)}</strong><br>"
            f'<span class="mono muted small">{ui.e(key.prefix)}</span></td>'
            f"<td>{state}</td><td class='mono'>{ui.e(ui.naira(key.spent_micro))}</td>"
            f"<td class='small muted'>{ui.e(ui.when(key.last_used_at))}</td><td class='right'>{action}</td></tr>"
        )
    return (
        '<div class="table-wrap"><table><thead><tr><th>Key</th><th>State</th>'
        "<th>Spent</th><th>Last used</th><th></th></tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table></div>"
    )


def _usage_rows(lines: tuple[UsageLine, ...]) -> str:
    if not lines:
        return '<div class="empty">No billed requests yet. The first one will appear here.</div>'
    rows = "".join(
        f"<tr><td class='mono small'>{ui.e(line.request_id[:15])}</td>"
        f"<td>{ui.e(line.model_alias)}<br><span class='small muted'>{ui.e(line.provider)}</span></td>"
        f"<td class='right'>{line.input_tokens:,} / {line.output_tokens:,}</td>"
        f"<td class='right mono'>{ui.e(ui.naira(line.charged_micro))}</td>"
        f"<td class='right small'>{line.latency_ms:,} ms</td>"
        f"<td class='small muted'>{ui.e(ui.when(line.created_at))}</td></tr>"
        for line in lines
    )
    return (
        '<div class="table-wrap"><table><thead><tr><th>Request</th><th>Model</th>'
        "<th class='right'>In / out tokens</th><th class='right'>Charged</th>"
        "<th class='right'>Latency</th><th>When</th></tr></thead><tbody>"
        + rows
        + "</tbody></table></div>"
    )


def _ledger_rows(lines: tuple[LedgerLine, ...]) -> str:
    if not lines:
        return '<div class="empty">No money has moved yet.</div>'
    rows = "".join(
        f"<tr><td>{ui.e(line.kind)}</td><td class='mono small'>{ui.e(line.reference[:22])}</td>"
        f"<td class='right mono'>{ui.e(ui.naira(line.amount_micro))}</td>"
        f"<td class='right mono'>{ui.e(ui.naira(line.balance_after_micro))}</td>"
        f"<td class='small muted'>{ui.e(ui.when(line.created_at))}</td></tr>"
        for line in lines
    )
    return (
        '<div class="table-wrap"><table><thead><tr><th>Kind</th><th>Reference</th>'
        "<th class='right'>Amount</th><th class='right'>Balance after</th><th>When</th>"
        "</tr></thead><tbody>" + rows + "</tbody></table></div>"
    )


def _page(
    request: Request,
    services: Services,
    account: AccountRecord,
    balance_micro: int,
    *,
    path: str,
    title: str,
    body: str,
    subtitle: str = "",
    status_code: int = 200,
    eyebrow: str = "Console",
) -> Response:
    csrf = token_for(request, services)
    settings: SiteSettings = request.state.settings
    html = ui.dashboard_page(
        settings,
        account=account,
        path=path,
        title=title,
        body=body,
        csrf=csrf,
        balance_micro=balance_micro,
        subtitle=subtitle,
        eyebrow=eyebrow,
    )
    return finish(request, services, csrf, html, status_code=status_code)


async def _signed_in(
    request: Request, services: Services
) -> tuple[AccountRecord, int] | Response:
    account = await accounts.account_for(request, services)
    if account is None:
        return redirect(f"/login?next={request.url.path}")
    balance = await services.dashboard.balance_micro(account.id)
    return account, balance


def install(app: APIRouter, services: Services) -> None:
    settings = services.settings

    # -- overview -------------------------------------------------------

    @app.get("/dashboard", include_in_schema=False)
    async def overview(request: Request) -> Response:
        who = await _signed_in(request, services)
        if isinstance(who, Response):
            return who
        account, balance = who
        keys = await services.keys.list_for_account(account.id)
        usage = await services.dashboard.usage(account.id, limit=5)
        now = services.clock.now()
        active = sum(1 for key in keys if key.revoked_at is None and key.is_usable(now))
        spent = sum(line.charged_micro for line in usage)
        banner = (
            '<div class="notice"><p><strong>Your wallet is empty.</strong> Create a key now, '
            'then add credit before your first request. <a href="/dashboard/activate">'
            "How activation works</a>.</p></div>"
            if balance == 0
            else ""
        )
        body = f"""{banner}
<div class="grid three">
<div class="card"><div class="stat-label">Balance</div><div class="stat">{ui.e(ui.naira(balance))}</div></div>
<div class="card"><div class="stat-label">Active keys</div><div class="stat">{active}</div>
<p class="small muted" style="margin:6px 0 0"><a href="/dashboard/keys">Manage keys</a></p></div>
<div class="card"><div class="stat-label">Recent spend</div><div class="stat">{ui.e(ui.naira(spent))}</div>
<p class="small muted" style="margin:6px 0 0">Last {len(usage)} requests</p></div>
</div>
<div class="card">
<div class="card-head"><div><h2>Recent requests</h2><p>Every row is a receipt: model, tokens, cost and latency.</p></div>
<a class="btn secondary small" href="/dashboard/requests">See all</a></div>
{_usage_rows(usage)}
</div>
<div class="grid two">
<div class="card"><div class="card-head"><div><h2>Send your first request</h2>
<p>Your base URL is ready. Use any OpenAI compatible client.</p></div></div>
<div class="copy-row"><pre id="overview-endpoint" class="nobreak">{ui.e(str(request.base_url).rstrip('/'))}/v1</pre>
<button class="btn secondary small" type="button" data-copy="#overview-endpoint">Copy</button></div>
<p class="small muted" style="margin:12px 0 0"><a href="/dashboard/setup">Full setup guide</a></p></div>
<div class="card"><div class="card-head"><div><h2>Account</h2><p>Signed in and ready.</p></div></div>
<dl class="kv"><div><dt>Email</dt><dd class="nobreak">{ui.e(account.email)}</dd></div>
<div><dt>Verification</dt><dd>{'<span class="badge green">verified</span>' if account.email_verified else '<span class="badge red">pending</span>'}</dd></div>
<div><dt>Plan</dt><dd>Pay as you go</dd></div></dl>
<p class="small muted" style="margin:14px 0 0"><a href="/dashboard/settings">Account settings</a></p></div>
</div>"""
        return _page(
            request, services, account, balance, path="/dashboard", title="Overview", body=body
        )

    @app.get("/dashboard/activate", include_in_schema=False)
    async def activate(request: Request) -> Response:
        who = await _signed_in(request, services)
        if isinstance(who, Response):
            return who
        account, balance = who
        if balance > 0:
            return redirect("/dashboard")
        rows = "".join(
            f'<div class="plan-row"><div><strong>{ui.e(name)}</strong>'
            f'<div class="small muted">{ui.e(note)}</div></div>'
            f'<div class="mono">{ui.e(ui.naira(price * 1_000_000))}<span class="small muted">/mo</span></div></div>'
            for name, price, note in PLANS
        )
        body = f"""<div class="notice"><p><strong>Payment comes first, on purpose.</strong>
We started with free tokens. People made many accounts, spent the credit in hours and left
us with the bill. So the rule is now simple: pick a plan or a top up, then use the gateway.
Credit you do not spend never expires.</p></div>
<div class="grid two">
<div class="card"><div class="card-head"><div><h2>Plans</h2><p>Billed monthly, cancelled any time.</p></div></div>
{rows}
</div>
<div class="card"><div class="card-head"><div><h2>Top up</h2><p>Pay as you go from {ui.naira(5_000 * 1_000_000)}.</p></div></div>
<p class="small muted">Card payments are not connected in this build. No button here will
pretend to charge you. Write to support and a person will credit the wallet and send a
receipt.</p>
<dl class="kv"><div><dt>Support</dt><dd>{ui.e(settings.support_email)}</dd></div>
<div><dt>Account</dt><dd class="mono small">{ui.e(account.id)}</dd></div>
<div><dt>Balance</dt><dd class="mono">{ui.e(ui.naira(balance))}</dd></div></dl>
<p style="margin:16px 0 0"><a class="btn" href="/support">Contact support</a></p>
</div>
</div>
<div class="card"><div class="card-head"><div><h2>What happens after payment</h2></div></div>
<ol class="small muted" style="margin:0;padding-left:18px;line-height:1.9">
<li>Support credits your wallet, in naira, as integer micro-naira.</li>
<li>Every API request reserves an amount, then settles at the real token cost.</li>
<li>If a request fails before the upstream answers, the reservation is released.</li>
<li>Each settled request writes one ledger line and one usage receipt.</li>
</ol></div>"""
        return _page(
            request,
            services,
            account,
            balance,
            path="/dashboard/activate",
            title="Activate your account",
            subtitle="One last step, then you are in.",
            body=body,
        )

    # -- setup and playground ------------------------------------------

    @app.get("/dashboard/setup", include_in_schema=False)
    async def setup(request: Request) -> Response:
        who = await _signed_in(request, services)
        if isinstance(who, Response):
            return who
        account, balance = who
        base = f"{str(request.base_url).rstrip('/')}/v1"
        chat = (
            f'curl {base}/chat/completions \\\n'
            '  -H "Authorization: Bearer $BREWKEG_API_KEY" \\\n'
            '  -H "Content-Type: application/json" \\\n'
            '  -d \'{"model":"claude-sonnet-5","messages":[{"role":"user","content":"hello"}]}\''
        )
        python = (
            "from openai import OpenAI\n\n"
            f'client = OpenAI(base_url="{base}", api_key="brewkeg_live_...")\n\n'
            "reply = client.chat.completions.create(\n"
            '    model="claude-sonnet-5",\n'
            '    messages=[{"role": "user", "content": "hello"}],\n'
            ")\n"
            "print(reply.choices[0].message.content)"
        )
        claude = (
            "export ANTHROPIC_BASE_URL=" + f"{str(request.base_url).rstrip('/')}/v1" + "\n"
            "export ANTHROPIC_AUTH_TOKEN=brewkeg_live_...\n"
            "claude"
        )
        body = f"""<div class="card"><div class="card-head"><div><h2>1. Create a key</h2>
<p>A key is shown once. We store only a hash, so we cannot show it again.</p></div>
<a class="btn" href="/dashboard/keys">Create a key</a></div>
<p class="small muted">Send it as <span class="mono">Authorization: Bearer ...</span>.
Never commit it. Revoke and replace it if it leaks.</p></div>
<div class="card"><div class="card-head"><div><h2>2. Point your client at the gateway</h2>
<p>One base URL, every provider.</p></div></div>
<div class="copy-row"><pre id="setup-base" class="nobreak">{ui.e(base)}</pre>
<button class="btn secondary small" type="button" data-copy="#setup-base">Copy</button></div>
</div>
<div class="card"><div class="card-head"><div><h2>3. Send a request</h2><p>Pick the client you already use.</p></div></div>
<h3>curl</h3><div class="copy-row"><pre id="setup-curl">{ui.e(chat)}</pre>
<button class="btn secondary small" type="button" data-copy="#setup-curl">Copy</button></div>
<h3 style="margin-top:22px">OpenAI Python SDK</h3>
<div class="copy-row"><pre id="setup-python">{ui.e(python)}</pre>
<button class="btn secondary small" type="button" data-copy="#setup-python">Copy</button></div>
<h3 style="margin-top:22px">Claude Code</h3>
<div class="copy-row"><pre id="setup-claude">{ui.e(claude)}</pre>
<button class="btn secondary small" type="button" data-copy="#setup-claude">Copy</button></div>
</div>
<div class="notice green"><p><strong>What comes back.</strong> Every response carries the
upstream body; the receipt is written to Requests. Streaming works the same way: tokens are
counted as they arrive and the hold is settled at the end.</p></div>"""
        return _page(
            request,
            services,
            account,
            balance,
            path="/dashboard/setup",
            title="Set up your harness",
            subtitle="Three steps, no SDK to install on our side.",
            body=body,
        )

    @app.get("/dashboard/playground", include_in_schema=False)
    async def playground(request: Request) -> Response:
        who = await _signed_in(request, services)
        if isinstance(who, Response):
            return who
        account, balance = who
        base = f"{str(request.base_url).rstrip('/')}/v1"
        sample = (
            "{\n"
            '  "model": "claude-sonnet-5",\n'
            '  "messages": [\n'
            '    {"role": "user", "content": "Explain a token receipt in one sentence."}\n'
            "  ]\n"
            "}"
        )
        body = f"""<div class="notice"><p>The playground builds the request. It does not
hold your key, so it will not send it for you. Copy the command and run it where your key
lives. This keeps a browser session from becoming an unlimited API credential.</p></div>
<div class="card"><div class="card-head"><div><h2>Request body</h2><p>Any OpenAI compatible field passes through.</p></div></div>
<div class="copy-row"><pre id="play-body">{ui.e(sample)}</pre>
<button class="btn secondary small" type="button" data-copy="#play-body">Copy body</button></div>
</div>
<div class="card"><div class="card-head"><div><h2>Run it</h2><p>Set the key in the environment, not in the command history.</p></div></div>
<div class="copy-row"><pre id="play-cmd">curl {ui.e(base)}/chat/completions \\
  -H "Authorization: Bearer $BREWKEG_API_KEY" \\
  -H "Content-Type: application/json" \\
  -d '$(cat body.json)'</pre>
<button class="btn secondary small" type="button" data-copy="#play-cmd">Copy</button></div>
<p class="small muted" style="margin:12px 0 0">Models list:
<a href="/v1/models" class="mono">{ui.e(base)}/models</a></p></div>"""
        return _page(
            request,
            services,
            account,
            balance,
            path="/dashboard/playground",
            title="Playground",
            subtitle="A request builder that never sees your key.",
            body=body,
        )

    # -- requests, context ---------------------------------------------

    @app.get("/dashboard/requests", include_in_schema=False)
    async def requests_page(request: Request) -> Response:
        who = await _signed_in(request, services)
        if isinstance(who, Response):
            return who
        account, balance = who
        lines = await services.dashboard.usage(account.id, limit=100)
        total = sum(line.charged_micro for line in lines)
        tokens = sum(line.input_tokens + line.output_tokens for line in lines)
        body = f"""<div class="grid three">
<div class="card"><div class="stat-label">Charged</div><div class="stat">{ui.e(ui.naira(total))}</div></div>
<div class="card"><div class="stat-label">Tokens</div><div class="stat">{tokens:,}</div></div>
<div class="card"><div class="stat-label">Requests</div><div class="stat">{len(lines)}</div></div>
</div>
<div class="card"><div class="card-head"><div><h2>Receipts</h2>
<p>Last {len(lines)} requests. No prompt or completion is stored.</p></div></div>
{_usage_rows(lines)}</div>"""
        return _page(
            request,
            services,
            account,
            balance,
            path="/dashboard/requests",
            title="Requests",
            body=body,
        )

    @app.get("/dashboard/context", include_in_schema=False)
    async def context_health(request: Request) -> Response:
        who = await _signed_in(request, services)
        if isinstance(who, Response):
            return who
        account, balance = who
        body = """<div class="card"><div class="card-head"><div><h2>What context health will show</h2>
<p>How full your agent's window is, and where the tokens went.</p></div></div>
<p class="small muted">The gateway already records input, output, cache read and cache write
tokens for every request. A per session view needs a session identifier from the client,
which not every harness sends. Until that exists, this page stays honest rather than showing
invented numbers.</p>
<hr class="rule"><dl class="kv">
<div><dt>Recorded today</dt><dd>tokens per request, by model and provider</dd></div>
<div><dt>Not recorded</dt><dd>prompt text, completion text, file contents</dd></div>
<div><dt>Where to look now</dt><dd><a href="/dashboard/requests">Requests</a></dd></div>
</dl></div>"""
        return _page(
            request,
            services,
            account,
            balance,
            path="/dashboard/context",
            title="Context health",
            body=body,
        )

    # -- keys -----------------------------------------------------------

    @app.get("/dashboard/keys", include_in_schema=False)
    async def keys_page(request: Request, created: str = "") -> Response:
        who = await _signed_in(request, services)
        if isinstance(who, Response):
            return who
        account, balance = who
        keys = await services.keys.list_for_account(account.id)
        csrf = token_for(request, services)
        body = f"""<div class="card"><div class="card-head"><div><h2>Create a key</h2>
<p>Name it for the machine or harness that will use it.</p></div></div>
<form class="stack" method="post" action="/api/keys" data-busy>
{ui.csrf_field(csrf)}
<div class="field-row"><div><label for="name">Name</label>
<input id="name" name="name" maxlength="60" placeholder="laptop, ci, claude code" required></div>
<div style="display:flex;align-items:flex-end"><button class="btn" type="submit">Create key</button></div></div>
</form></div>
<div class="card"><div class="card-head"><div><h2>Your keys</h2>
<p>Prefix only. The secret is shown once, at creation.</p></div></div>
{_key_rows(keys, services.clock.now(), csrf)}</div>"""
        return _page(
            request,
            services,
            account,
            balance,
            path="/dashboard/keys",
            title="API keys",
            body=body,
        )

    @app.post("/api/keys")
    async def create_key(request: Request) -> Response:
        account = await accounts.account_for(request, services)
        if account is None:
            return error_response(request, "Sign in first.", status_code=401)
        if not await submitted_csrf(request, cookie_name=services.csrf_cookie):
            return error_response(request, "This form expired. Reload and try again.", status_code=403)
        fields = await _fields(request)
        name = fields.get("name", "").strip()
        if not name or len(name) > 60:
            return error_response(
                request, "Give the key a name of 1 to 60 characters.", status_code=400
            )
        try:
            generated = await issue_key_with_retry(
                request=NewKeyRequest(account_id=account.id, name=name),
                store=services.keys,
                pepper=settings.key_pepper,
                product=settings.key_prefix,
                environment=settings.key_environment,
                new_id=str(uuid.uuid4()),
            )
        except Exception as error:
            return error_response(request, f"The key could not be created: {error}", status_code=400)
        if wants_json(request):
            return JSONResponse(
                {
                    "key": generated.raw,
                    "prefix": generated.prefix,
                    "name": name,
                    "note": "This is the only time the full key is shown.",
                },
                status_code=201,
            )
        balance = await services.dashboard.balance_micro(account.id)
        keys = await services.keys.list_for_account(account.id)
        csrf = token_for(request, services)
        body = f"""<div class="keybox"><strong>Copy this key now.</strong>
<p class="small" style="margin:6px 0 0">It will not be shown again. We store only a hash.</p>
<div class="copy-row"><code id="new-key" class="nobreak">{ui.e(generated.raw)}</code>
<button class="btn secondary small" type="button" data-copy="#new-key">Copy</button></div></div>
<div class="card"><div class="card-head"><div><h2>Your keys</h2>
<p>Name it for the machine or harness that will use it.</p></div>
<a class="btn secondary small" href="/dashboard/keys">Back to create</a></div>
{_key_rows(keys, services.clock.now(), csrf)}</div>"""
        return _page(
            request,
            services,
            account,
            balance,
            path="/dashboard/keys",
            title="API key created",
            body=body,
        )

    @app.post("/api/keys/{key_id}/revoke")
    async def revoke_key(request: Request, key_id: str) -> Response:
        account = await accounts.account_for(request, services)
        if account is None:
            return error_response(request, "Sign in first.", status_code=401)
        if not await submitted_csrf(request, cookie_name=services.csrf_cookie):
            return error_response(request, "This form expired.", status_code=403)
        record = await services.keys.by_id(key_id)
        if record is None or record.account_id != account.id:
            return error_response(request, "No such key.", status_code=404)
        await services.keys.revoke(record.id, services.clock.now())
        return redirect("/dashboard/keys?revoked=1")

    # -- money ----------------------------------------------------------

    @app.get("/dashboard/billing", include_in_schema=False)
    async def billing(request: Request) -> Response:
        who = await _signed_in(request, services)
        if isinstance(who, Response):
            return who
        account, balance = who
        lines = await services.dashboard.ledger(account.id, limit=50)
        body = f"""<div class="grid two">
<div class="card"><div class="stat-label">Balance</div>
<div class="stat">{ui.e(ui.naira(balance))}</div>
<p class="small muted" style="margin:8px 0 0">Prepaid. Never expires. Shared by every key.</p></div>
<div class="card"><div class="card-head"><div><h2>Add credit</h2>
<p>Card payments are not connected in this build.</p></div></div>
<p class="small muted">Write to {ui.e(settings.support_email)} with the account id below.
Support credits the wallet and sends a receipt. No fake checkout buttons here.</p>
<dl class="kv"><div><dt>Account</dt><dd class="mono small">{ui.e(account.id)}</dd></div>
<div><dt>Minimum top up</dt><dd>{ui.e(ui.naira(5_000 * 1_000_000))}</dd></div></dl></div>
</div>
<div class="card"><div class="card-head"><div><h2>Ledger</h2>
<p>Append only. Every line has a reference you can quote to support.</p></div></div>
{_ledger_rows(lines)}</div>"""
        return _page(
            request,
            services,
            account,
            balance,
            path="/dashboard/billing",
            title="Billing",
            subtitle="One wallet, every provider.",
            body=body,
        )

    # -- referrals, team, experiments, models, data --------------------

    @app.get("/dashboard/referrals", include_in_schema=False)
    async def referrals(request: Request) -> Response:
        who = await _signed_in(request, services)
        if isinstance(who, Response):
            return who
        account, balance = who
        from tokn_core.auth.hashing import hash_secret

        code = hash_secret(account.id, settings.session_secret)[:10].upper()
        link = f"{str(request.base_url).rstrip('/')}/signup?ref={code}"
        body = f"""<div class="card"><div class="card-head"><div><h2>Your invite link</h2>
<p>Share it with someone who sends a lot of tokens.</p></div></div>
<div class="copy-row"><pre id="ref-link" class="nobreak">{ui.e(link)}</pre>
<button class="btn secondary small" type="button" data-copy="#ref-link">Copy</button></div>
<p class="small muted" style="margin:12px 0 0">Code <span class="mono">{ui.e(code)}</span>.
Credit for referrals is not switched on yet, so this page makes no earnings promise.</p></div>
<div class="card"><div class="card-head"><div><h2>How referrals will work</h2></div></div>
<ol class="small muted" style="margin:0;padding-left:18px;line-height:1.9">
<li>Someone signs up with your link and tops up.</li>
<li>After their first settled request, both wallets receive a credit.</li>
<li>The credit is a ledger line, so it never becomes an untraceable balance.</li>
</ol></div>"""
        return _page(
            request,
            services,
            account,
            balance,
            path="/dashboard/referrals",
            title="Referrals",
            body=body,
        )

    @app.get("/dashboard/team", include_in_schema=False)
    async def team(request: Request) -> Response:
        who = await _signed_in(request, services)
        if isinstance(who, Response):
            return who
        account, balance = who
        body = f"""<div class="card"><div class="card-head"><div><h2>One account per login</h2>
<p>Team seats are not built yet. This page says so instead of showing dead invite buttons.</p></div></div>
<p class="small muted">Today a wallet belongs to one account. If your team needs a shared
spend view, write to {ui.e(settings.support_email)} and describe the shape you need: seats,
separate keys per person, or per project budgets. Keys can already be named per person and
revoked individually.</p>
<dl class="kv"><div><dt>Keys</dt><dd><a href="/dashboard/keys">Manage</a></dd></div>
<div><dt>Spend by key</dt><dd>Shown on the keys page</dd></div>
<div><dt>Support</dt><dd>{ui.e(settings.support_email)}</dd></div></dl></div>"""
        return _page(
            request,
            services,
            account,
            balance,
            path="/dashboard/team",
            title="Team",
            body=body,
        )

    @app.get("/dashboard/experiments", include_in_schema=False)
    async def experiments(request: Request) -> Response:
        who = await _signed_in(request, services)
        if isinstance(who, Response):
            return who
        account, balance = who
        body = """<div class="card"><div class="card-head"><div><h2>Routing experiments</h2>
<p>Not shipped. Here is what is already true and what is not.</p></div></div>
<dl class="kv">
<div><dt>Model fallback</dt><dd>The gateway falls back when an upstream fails</dd></div>
<div><dt>Circuit breaking</dt><dd>Per provider, so one bad upstream cannot stall the rest</dd></div>
<div><dt>A/B prompt tests</dt><dd>Not built. No split traffic, no scores</dd></div>
<div><dt>Custom aliases</dt><dd>Not exposed yet</dd></div></dl>
<p class="small muted" style="margin-top:14px">When experiments ship they will write to the
same usage receipt, so cost stays comparable across variants.</p></div>"""
        return _page(
            request,
            services,
            account,
            balance,
            path="/dashboard/experiments",
            title="Experiments",
            body=body,
        )

    @app.get("/dashboard/uncensored", include_in_schema=False)
    async def uncensored(request: Request) -> Response:
        who = await _signed_in(request, services)
        if isinstance(who, Response):
            return who
        account, balance = who
        body = """<div class="card"><div class="card-head"><div><h2>Uncensored models</h2>
<p>Open weight models with fewer refusal layers, clearly labelled.</p></div></div>
<p class="small muted">The public catalogue marks these models. They cost more because the
upstreams charge more, and they are billed at the same transparent per token rate as
everything else. This console does not keep a second model list that can drift: the gateway's
<strong>/v1/models</strong> is the source of truth.</p>
<dl class="kv"><div><dt>Price</dt><dd>Shown per model in the catalogue</dd></div>
<div><dt>Receipts</dt><dd>Identical shape to every other model</dd></div>
<div><dt>Catalogue</dt><dd><a href="/uncensored">Read the public page</a></dd></div></dl></div>"""
        return _page(
            request,
            services,
            account,
            balance,
            path="/dashboard/uncensored",
            title="Uncensored models",
            body=body,
        )

    @app.get("/dashboard/data-protection", include_in_schema=False)
    async def data_protection(request: Request) -> Response:
        who = await _signed_in(request, services)
        if isinstance(who, Response):
            return who
        account, balance = who
        body = f"""<div class="card"><div class="card-head"><div><h2>What we store</h2>
<p>Metadata for billing, not your prompts.</p></div></div>
<dl class="kv">
<div><dt>Stored</dt><dd>tokens, model, provider, cost, latency, outcome</dd></div>
<div><dt>Not stored</dt><dd>prompt text, completion text, uploaded files</dd></div>
<div><dt>Keys</dt><dd>HMAC SHA-256 hash and visible prefix only</dd></div>
<div><dt>Passwords</dt><dd>Argon2id hashes, never the password</dd></div>
<div><dt>Sessions</dt><dd>keyed digest, server side, thirty minute idle timeout</dd></div>
</dl></div>
<div class="card"><div class="card-head"><div><h2>Deletion and export</h2>
<p>Ask a person. No automated button that quietly fails.</p></div></div>
<p class="small muted">Write to {ui.e(settings.support_email)} from the account email. We will
send a machine readable export of receipts and ledger lines, then delete the account and its
sessions. Ledger lines that back a paid balance are retained as long as tax rules require.</p>
<p style="margin-top:14px"><a class="btn secondary" href="mailto:{ui.e(settings.support_email)}">Email support</a></p></div>"""
        return _page(
            request,
            services,
            account,
            balance,
            path="/dashboard/data-protection",
            title="Data protection",
            body=body,
        )

    # -- settings, feedback --------------------------------------------

    @app.get("/dashboard/settings", include_in_schema=False)
    async def account_settings(request: Request, changed: str = "") -> Response:
        who = await _signed_in(request, services)
        if isinstance(who, Response):
            return who
        account, balance = who
        csrf = token_for(request, services)
        notice = (
            '<div class="notice green"><p>Password changed. Other sessions were signed out.</p></div>'
            if changed
            else ""
        )
        body = f"""{notice}
<div class="card"><div class="card-head"><div><h2>Account</h2>
<p>Change the email by asking support: it needs a verification round trip.</p></div></div>
<dl class="kv"><div><dt>Email</dt><dd class="nobreak">{ui.e(account.email)}</dd></div>
<div><dt>Account id</dt><dd class="mono small">{ui.e(account.id)}</dd></div>
<div><dt>Status</dt><dd>{ui.e(account.status)}</dd></div>
<div><dt>Admin</dt><dd>{'yes' if account.is_admin else 'no'}</dd></div></dl></div>
<div class="card"><div class="card-head"><div><h2>Change password</h2>
<p>Changing it signs out every other browser.</p></div></div>
<form class="stack" method="post" action="/api/account/password" data-busy>
{ui.csrf_field(csrf)}
<div><label for="current">Current password</label>
<input id="current" name="current" type="password" autocomplete="current-password" required></div>
<div class="field-row"><div><label for="new">New password</label>
<input id="new" name="new" type="password" autocomplete="new-password" required></div>
<div><label for="confirm">Confirm new password</label>
<input id="confirm" name="confirm" type="password" autocomplete="new-password" required></div></div>
<button class="btn" type="submit">Update password</button>
</form></div>
<div class="card"><div class="card-head"><div><h2>Other sessions</h2>
<p>End every session except this one.</p></div></div>
<form method="post" action="/api/account/sessions/revoke">
{ui.csrf_field(csrf)}
<button class="btn secondary" type="submit" data-confirm="Sign out every other browser?">Sign out everywhere else</button>
</form></div>"""
        return _page(
            request,
            services,
            account,
            balance,
            path="/dashboard/settings",
            title="Settings",
            body=body,
        )

    @app.post("/api/account/password")
    async def change_password(request: Request) -> Response:
        account = await accounts.account_for(request, services)
        if account is None:
            return error_response(request, "Sign in first.", status_code=401)
        if not await submitted_csrf(request, cookie_name=services.csrf_cookie):
            return error_response(request, "This form expired.", status_code=403)
        fields = await _fields(request)
        try:
            await accounts.change_password(
                services,
                account=account,
                current=fields.get("current", ""),
                new=fields.get("new", ""),
                confirmation=fields.get("confirm", ""),
                keep_digest=getattr(request.state, "session_digest", None),
            )
        except accounts.AccountError as error:
            return error_response(request, error.message, status_code=400)
        return redirect("/dashboard/settings?changed=1")

    @app.post("/api/account/sessions/revoke")
    async def revoke_sessions(request: Request) -> Response:
        account = await accounts.account_for(request, services)
        if account is None:
            return error_response(request, "Sign in first.", status_code=401)
        if not await submitted_csrf(request, cookie_name=services.csrf_cookie):
            return error_response(request, "This form expired.", status_code=403)
        await services.auth.delete_sessions(
            account.id, keep=getattr(request.state, "session_digest", None)
        )
        return redirect("/dashboard/settings")

    @app.get("/dashboard/feedback", include_in_schema=False)
    async def feedback_page(request: Request, sent: str = "") -> Response:
        who = await _signed_in(request, services)
        if isinstance(who, Response):
            return who
        account, balance = who
        csrf = token_for(request, services)
        notice = (
            '<div class="notice green"><p>Thank you. It is in the queue, with your email on it.</p></div>'
            if sent
            else ""
        )
        body = f"""{notice}<div class="card"><div class="card-head"><div><h2>Suggestion box</h2>
<p>What is slow, confusing or missing? One box, read by a person.</p></div></div>
<form class="stack" method="post" action="/api/feedback" data-busy>
{ui.csrf_field(csrf)}
<div><label for="message">Your message</label>
<textarea id="message" name="message" maxlength="4000" placeholder="The thing I wish this page did..." required></textarea></div>
<button class="btn" type="submit">Send suggestion</button>
</form></div>"""
        return _page(
            request,
            services,
            account,
            balance,
            path="/dashboard/feedback",
            title="Suggestion box",
            body=body,
        )

    @app.post("/api/feedback")
    async def feedback(request: Request) -> Response:
        account = await accounts.account_for(request, services)
        if account is None:
            return error_response(request, "Sign in first.", status_code=401)
        if not await submitted_csrf(request, cookie_name=services.csrf_cookie):
            return error_response(request, "This form expired.", status_code=403)
        fields = await _fields(request)
        message = fields.get("message", "").strip()
        if len(message) < 3:
            return error_response(request, "Write a little more.", status_code=400)
        await services.dashboard.add_feedback(
            account_id=account.id, email=account.email, page="dashboard", message=message
        )
        if wants_json(request):
            return JSONResponse({"status": "received"}, status_code=201)
        return redirect("/dashboard/feedback?sent=1")

    # -- read-only JSON -------------------------------------------------

    @app.get("/api/wallet")
    async def wallet_json(request: Request) -> Response:
        account = await accounts.account_for(request, services)
        if account is None:
            return JSONResponse({"error": "sign in"}, status_code=401)
        balance = await services.dashboard.balance_micro(account.id)
        lines = await services.dashboard.ledger(account.id, limit=20)
        return JSONResponse(
            {
                "balance_micro": balance,
                "currency": "NGN",
                "ledger": [
                    {
                        "kind": line.kind,
                        "amount_micro": line.amount_micro,
                        "balance_after_micro": line.balance_after_micro,
                        "reference": line.reference,
                        "created_at": line.created_at.isoformat(),
                    }
                    for line in lines
                ],
            }
        )

    @app.get("/api/requests")
    async def requests_json(request: Request) -> Response:
        account = await accounts.account_for(request, services)
        if account is None:
            return JSONResponse({"error": "sign in"}, status_code=401)
        lines = await services.dashboard.usage(account.id, limit=100)
        return JSONResponse(
            {
                "requests": [
                    {
                        "request_id": line.request_id,
                        "model": line.model_alias,
                        "provider": line.provider,
                        "input_tokens": line.input_tokens,
                        "output_tokens": line.output_tokens,
                        "charged_micro": line.charged_micro,
                        "latency_ms": line.latency_ms,
                        "status": line.status,
                        "created_at": line.created_at.isoformat(),
                    }
                    for line in lines
                ]
            }
        )

    @app.get("/api/keys")
    async def keys_json(request: Request) -> Response:
        account = await accounts.account_for(request, services)
        if account is None:
            return JSONResponse({"error": "sign in"}, status_code=401)
        keys = await services.keys.list_for_account(account.id)
        return JSONResponse(
            {
                "keys": [
                    {
                        "id": key.id,
                        "name": key.name,
                        "prefix": key.prefix,
                        "spent_micro": key.spent_micro,
                        "revoked": key.revoked_at is not None,
                        "last_used_at": None
                        if key.last_used_at is None
                        else key.last_used_at.isoformat(),
                    }
                    for key in keys
                ]
            }
        )

    # -- admin ----------------------------------------------------------

    @app.get("/admin", include_in_schema=False)
    async def admin_page(request: Request, saved: str = "") -> Response:
        account = await accounts.account_for(request, services)
        if account is None:
            return redirect("/login?next=/admin")
        if not account.is_admin:
            return _forbidden(request, services, account)
        site: SiteSettings = request.state.settings
        balance = await services.dashboard.balance_micro(account.id)
        csrf = token_for(request, services)
        notice = (
            '<div class="notice green"><p>Saved. The new theme is live on the next request.</p></div>'
            if saved
            else ""
        )
        colour = (
            ("color_ink", "Ink", site.color_ink),
            ("color_paper", "Paper", site.color_paper),
            ("color_accent", "Green accent", site.color_accent),
            ("color_accent_deep", "Deep green", site.color_accent_deep),
            ("color_highlight", "Blue highlight", site.color_highlight),
        )
        colours = "".join(
            f'<div class="colour-field"><input type="color" id="{name}" name="{name}" '
            f'value="{ui.e(value)}"><label for="{name}" style="margin:0">{ui.e(label)}</label></div>'
            for name, label, value in colour
        )
        font_options = "".join(
            f'<option value="{ui.e(name)}"{" selected" if name == site.font else ""}>'
            f"{ui.e(name.title())}</option>"
            for name in FONTS
        )
        feedback = await services.dashboard.feedback(limit=20)
        feedback_rows = (
            "".join(
                f"<tr><td>{ui.e(line.page)}</td><td>{ui.e(line.message)}</td>"
                f"<td class='small muted'>{ui.e(ui.when(line.created_at))}</td></tr>"
                for line in feedback
            )
            or '<tr><td colspan="3" class="muted">Nothing yet.</td></tr>'
        )
        dev_note = (
            '<div class="notice"><p>Development mode: every account is an admin. Set '
            "<span class=\\\"mono\\\">TOKN_ADMIN_EMAILS</span> and "
            "<span class=\\\"mono\\\">TOKN_ENV=production</span> before you expose this.</p></div>"
            if settings.env == "development"
            else ""
        )
        body = f"""{notice}{dev_note}
<div class="grid two">
<div class="card"><div class="card-head"><div><h2>Site</h2>
<p>Availability, name and copy the console can change.</p></div></div>
<form class="stack" method="post" action="/admin">
{ui.csrf_field(csrf)}
<label class="check"><input type="checkbox" name="available" value="1"{" checked" if site.available else ""}> Site is available</label>
<div><label for="maintenance_message">Maintenance message</label>
<input id="maintenance_message" name="maintenance_message" value="{ui.e(site.maintenance_message)}" maxlength="200"></div>
<div class="field-row"><div><label for="name">Name</label>
<input id="name" name="name" value="{ui.e(site.name)}" maxlength="40" required></div>
<div><label for="mark">Mark</label>
<input id="mark" name="mark" value="{ui.e(site.mark)}" maxlength="2" required></div></div>
<div><label for="tagline">Tagline</label>
<input id="tagline" name="tagline" value="{ui.e(site.tagline)}" maxlength="120"></div>
<div><label for="hero_body">Hero body</label>
<textarea id="hero_body" name="hero_body" maxlength="600">{ui.e(site.hero_body)}</textarea></div>
<div><label for="support_email">Support email</label>
<input id="support_email" name="support_email" type="email" value="{ui.e(site.support_email)}"></div>
<button class="btn" type="submit">Save site</button>
</form></div>
<div class="card"><div class="card-head"><div><h2>Theme</h2>
<p>Colours and font reach /theme.css, so every page follows.</p></div></div>
<form class="stack" method="post" action="/admin">
{ui.csrf_field(csrf)}
<input type="hidden" name="only" value="theme">
<div class="colour-grid">{colours}</div>
<div><label for="font">Font</label><select id="font" name="font">{font_options}</select></div>
<button class="btn" type="submit">Save theme</button>
</form>
<p class="small muted" style="margin:14px 0 0">The mirrored marketing copy lives in its
own HTML, so name, tagline and body apply to this console. Colours and font apply to every
page through the theme stylesheet.</p></div>
</div>
<div class="card"><div class="card-head"><div><h2>Suggestions</h2>
<p>Newest first. This is the whole inbox.</p></div></div>
<div class="table-wrap"><table><thead><tr><th>Page</th><th>Message</th><th>When</th></tr></thead>
<tbody>{feedback_rows}</tbody></table></div></div>"""
        return _page(
            request,
            services,
            account,
            balance,
            path="/admin",
            title="Site settings",
            subtitle="What the operator can change without a deploy.",
            eyebrow="Admin",
            body=body,
        )

    @app.post("/admin")
    async def admin_save(request: Request) -> Response:
        account = await accounts.account_for(request, services)
        if account is None:
            return redirect("/login?next=/admin")
        if not account.is_admin:
            return _forbidden(request, services, account)
        if not await submitted_csrf(request, cookie_name=services.csrf_cookie):
            return error_response(request, "This form expired.", status_code=403)
        fields = await _fields(request)
        current: SiteSettings = request.state.settings
        try:
            if fields.get("only") == "theme":
                updated = current.with_changes(
                    color_ink=fields.get("color_ink", current.color_ink),
                    color_paper=fields.get("color_paper", current.color_paper),
                    color_accent=fields.get("color_accent", current.color_accent),
                    color_accent_deep=fields.get("color_accent_deep", current.color_accent_deep),
                    color_highlight=fields.get("color_highlight", current.color_highlight),
                    font=fields.get("font", current.font),
                )
            else:
                updated = current.with_changes(
                    available=fields.get("available", "") == "1",
                    maintenance_message=fields.get(
                        "maintenance_message", current.maintenance_message
                    ),
                    name=fields.get("name", current.name),
                    mark=fields.get("mark", current.mark),
                    tagline=fields.get("tagline", current.tagline),
                    hero_body=fields.get("hero_body", current.hero_body),
                    support_email=fields.get("support_email", current.support_email),
                )
        except ValueError as error:
            return error_response(request, str(error), status_code=400)
        await services.site.save(updated)
        return redirect("/admin?saved=1")


async def _fields(request: Request) -> dict[str, str]:
    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        try:
            payload = await request.json()
        except ValueError:
            return {}
        if not isinstance(payload, dict):
            return {}
        return {str(key): str(value) for key, value in payload.items() if value is not None}
    form = await request.form()
    result: dict[str, str] = {}
    for key, value in form.items():
        result[key] = value if isinstance(value, str) else ""
    return result


def _forbidden(request: Request, services: Services, account: AccountRecord) -> Response:
    balance = 0
    csrf = token_for(request, services)
    settings: SiteSettings = request.state.settings
    html = ui.dashboard_page(
        settings,
        account=account,
        path="/admin",
        title="Not allowed",
        body=(
            '<div class="card"><h2>Admins only</h2><p class="muted">This account is not an '
            "administrator. Ask the operator to add your email to the admin list.</p>"
            '<a class="btn secondary" href="/dashboard">Back to the console</a></div>'
        ),
        csrf=csrf,
        balance_micro=balance,
        eyebrow="Admin",
    )
    return finish(request, services, csrf, html, status_code=403)
