"""HWPX Model Context Protocol server package."""

from importlib.metadata import version, PackageNotFoundError

try:  # pragma: no cover - metadata lookup is cached by packaging
    __version__ = version("hwpx-mcp-server")
except PackageNotFoundError:  # pragma: no cover - fallback for local development
    __version__ = "0.0.0"

__all__ = ["__version__"]
