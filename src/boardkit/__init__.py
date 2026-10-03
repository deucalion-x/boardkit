"""A local-first command-line client for repository-owned GitHub Projects."""

from importlib.metadata import PackageNotFoundError, version

__all__ = ["__version__"]

try:
    __version__ = version("boardkit")
except PackageNotFoundError:  # Running directly from an unpackaged source tree.
    __version__ = "0+unknown"
