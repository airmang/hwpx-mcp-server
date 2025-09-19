"""HWPX Model Context Protocol 서버 패키지."""

from importlib.metadata import version, PackageNotFoundError

try:  # pragma: no cover - metadata lookup is cached by packaging
    __version__ = version("hwpx-mcp-server")
except PackageNotFoundError:  # pragma: no cover - fallback for local development
    __version__ = "0.0.0"

__all__ = ["__version__"]
