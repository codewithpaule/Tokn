"""__init__ for the console package.

The console now only hosts the site and the theme stylesheet.
"""

from tokn_console.app import create_app

__all__ = ["create_app"]
