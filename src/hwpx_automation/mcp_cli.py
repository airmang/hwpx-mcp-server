# SPDX-License-Identifier: Apache-2.0
"""Guarded entry point for the optional MCP adapter."""

from __future__ import annotations

import sys
from collections.abc import Sequence

_INSTALL_HINT = (
    'MCP support is optional. Install it with '
    '"python-hwpx-automation[mcp]" and retry.'
)


def main(argv: Sequence[str] | None = None) -> None:
    """Start the MCP adapter or fail with an actionable extra-install hint."""

    try:
        from .server import main as server_main
    except ModuleNotFoundError as exc:
        if exc.name == "mcp" or (exc.name or "").startswith("mcp."):
            print(_INSTALL_HINT, file=sys.stderr)
            raise SystemExit(2) from exc
        raise
    server_main(list(argv) if argv is not None else None)


if __name__ == "__main__":
    main()
