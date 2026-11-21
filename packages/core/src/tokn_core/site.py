"""What the operator can change without a deploy.

One row, one object. Every page reads it, the theme route renders it as CSS, and the
admin screen edits it. Copy lives here rather than in templates so a wording change never
needs a release.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace

HEX = re.compile(r"^#[0-9a-fA-F]{6}$")

FONTS = {
    "montserrat": '"Montserrat", "Montserrat Fallback", system-ui, sans-serif',
    "system": 'system-ui, -apple-system, "Segoe UI", Roboto, sans-serif',
    "mono": '"Montserrat", ui-monospace, monospace',
}


@dataclass(frozen=True, slots=True)
class SiteSettings:
    """The whole site's knobs. Defaults are the shipped values."""

    available: bool = True
    maintenance_message: str = "We are making a change. Back shortly."

    name: str = "Tokn"
    mark: str = "T"
    tagline: str = "Your coding agent. Paid by the token."
    hero_body: str = (
        "Point the agent you already use at one key. Pay in naira for what you use, see a "
        "receipt for every request, and keep any credit you do not spend."
    )
    support_email: str = "support@tokn.ng"

    color_ink: str = "#0e1210"
    color_paper: str = "#fbfbf8"
    color_accent: str = "#1e6b4b"
    color_accent_deep: str = "#154d36"
    color_highlight: str = "#e3b93c"
    font: str = "montserrat"

    def validated(self) -> SiteSettings:
        for name in (
            "color_ink",
            "color_paper",
            "color_accent",
            "color_accent_deep",
            "color_highlight",
        ):
            value = getattr(self, name)
            if not HEX.match(value):
                raise ValueError(f"{name} must be a #rrggbb colour, got {value!r}")
        if self.font not in FONTS:
            raise ValueError(f"unknown font: {self.font}")
        if not self.name.strip():
            raise ValueError("the site needs a name")
        if not self.mark.strip():
            raise ValueError("the mark needs at least one character")
        return self

    def with_changes(self, **changes: object) -> SiteSettings:
        """Copy with changes applied, still validated."""
        return replace(self, **changes).validated()  # type: ignore[arg-type]

    def font_stack(self) -> str:
        return FONTS[self.font]

    def theme_css(self) -> str:
        """The subset of tokens the operator controls, as CSS."""
        return (
            "\n".join(
                (
                    ":root{",
                    f"--ink:{self.color_ink};",
                    f"--paper:{self.color_paper};",
                    f"--green:{self.color_accent};",
                    f"--green-deep:{self.color_accent_deep};",
                    f"--yellow:{self.color_highlight};",
                    f"--font-sans:{self.font_stack()};",
                    "}",
                )
            )
            + "\n"
        )
