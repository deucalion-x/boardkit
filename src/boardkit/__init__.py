"""Query a GitHub Project board, and keep its issues reviewable from git.

The strategy this package exists to enforce is to filter at the server and
then read. Nothing here is clever; what it buys is that every query narrows
before it prints, and that the repo-specific names — the project number, the
custom field names, the status columns — live in one config table instead of
being spread across a handful of scripts.
"""

__all__ = ["__version__"]
__version__ = "0.1.0"
