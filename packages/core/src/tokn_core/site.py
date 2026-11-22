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

    name: str = "Brewkeg"
    mark: str = "B"
    tagline: str = "Your coding agent. Paid by the token."
    hero_body: str = (
        "Point the agent you already use at one key. Pay in naira for what you use, see a "
        "receipt for every request, and keep any credit you do not spend."
    )
    support_email: str = "support@brewkeg.dev"

    color_ink: str = "#0E1A24"
    color_paper: str = "#F7F9FC"
    color_accent: str = "#1F6B4A"
    color_accent_deep: str = "#17513A"
    color_highlight: str = "#17497F"
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
        """The operator's tokens, written twice.

        The first block is ours (``--ink``, ``--paper``, ``--green``, ``--yellow``).
        The second maps them onto the token names the mirrored design already uses, so a
        colour changed in the admin screen reaches every page, including theirs.
        """
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
                    f"--background:{self.color_paper};",
                    f"--foreground:{self.color_ink};",
                    "--card:#ffffff;",
                    f"--card-foreground:{self.color_ink};",
                    "--popover:#ffffff;",
                    f"--popover-foreground:{self.color_ink};",
                    f"--primary:{self.color_highlight};",
                    "--primary-foreground:#ffffff;",
                    "--secondary:#eaf0f6;",
                    f"--secondary-foreground:{self.color_ink};",
                    "--muted:#eef2f7;",
                    "--muted-foreground:#56687a;",
                    f"--accent:{self.color_accent};",
                    "--accent-foreground:#ffffff;",
                    "--border:#dbe3ec;",
                    "--input:#dbe3ec;",
                    f"--ring:{self.color_highlight};",
                    "--sidebar:#ffffff;",
                    f"--sidebar-foreground:{self.color_ink};",
                    f"--sidebar-primary:{self.color_highlight};",
                    "--sidebar-primary-foreground:#ffffff;",
                    f"--sidebar-accent:{self.color_accent};",
                    "--sidebar-accent-foreground:#ffffff;",
                    "--sidebar-border:#dbe3ec;",
                    "--radius:.5rem;",
                    "}",
                )
            )
            + "\n"
        )
