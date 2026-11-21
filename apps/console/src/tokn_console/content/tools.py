"""Connect guides, as data.

One module rather than four near-identical templates. Each entry is what a person
actually needs: where the setting lives, what to put there, and what goes wrong.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class Tool:
    slug: str
    name: str
    mark: str
    protocol: str
    summary: str
    config: tuple[tuple[str, str], ...]
    steps: tuple[str, ...]
    limits: tuple[str, ...] = field(default=())


TOOLS: tuple[Tool, ...] = (
    Tool(
        slug="claude-code",
        name="Claude Code",
        mark="CC",
        protocol="Anthropic",
        summary="Anthropic Messages protocol, including streaming and tool calls.",
        config=(
            ("ANTHROPIC_BASE_URL", "https://{domain}"),
            ("ANTHROPIC_AUTH_TOKEN", "tokn_live_…"),
        ),
        steps=(
            "Create a key in the console and copy it once, while it is shown.",
            "Export the two variables above in the shell you start Claude Code from.",
            "Run claude as usual. The first request appears in Usage within a second.",
        ),
        limits=(
            "Claude Code features that call Anthropic endpoints directly, outside the "
            "configured base URL, will not be routed through Tokn.",
        ),
    ),
    Tool(
        slug="codex",
        name="Codex CLI",
        mark="CX",
        protocol="OpenAI",
        summary="OpenAI Chat Completions, streaming.",
        config=(
            ("OPENAI_BASE_URL", "https://{domain}/v1"),
            ("OPENAI_API_KEY", "tokn_live_…"),
        ),
        steps=(
            "Create a key in the console.",
            "Set the two variables above, or put them in the Codex config file.",
            "Run the CLI. Every completion is metered and receipted.",
        ),
    ),
    Tool(
        slug="opencode",
        name="OpenCode",
        mark="OC",
        protocol="OpenAI",
        summary="Any OpenAI-compatible agent, including OpenCode's custom provider.",
        config=(
            ("baseURL", "https://{domain}/v1"),
            ("apiKey", "tokn_live_…"),
        ),
        steps=(
            "Add a custom provider with the base URL and key above.",
            "Select the model by its catalogue alias, not by the upstream name.",
            "Start a session; the receipt appears in Usage as it streams.",
        ),
    ),
    Tool(
        slug="other",
        name="Cline, Roo, Continue, Cursor",
        mark="··",
        protocol="OpenAI or Anthropic",
        summary="Anything that accepts a custom base URL.",
        config=(
            ("Base URL", "https://{domain}/v1"),
            ("API key", "tokn_live_…"),
        ),
        steps=(
            "Choose the OpenAI-compatible provider in the client's settings.",
            "Paste the base URL and the key.",
            "Pick a model alias from the catalogue.",
        ),
        limits=(
            "Some features in these clients use their own hosted services and cannot be "
            "pointed elsewhere. Those calls do not pass through Tokn and are not billed here.",
        ),
    ),
)


def by_slug(slug: str) -> Tool | None:
    return next((tool for tool in TOOLS if tool.slug == slug), None)


def config_rows(tool: Tool, domain: str) -> tuple[tuple[str, str], ...]:
    return tuple((key, value.format(domain=domain)) for key, value in tool.config)
