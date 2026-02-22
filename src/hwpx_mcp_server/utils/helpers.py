from __future__ import annotations

import os
from pathlib import Path


def default_max_chars() -> int:
    raw = os.environ.get("HWPX_MCP_MAX_CHARS", "10000")
    try:
        value = int(raw)
    except ValueError:
        value = 10000
    return max(1, value)


MAX_CHARS = default_max_chars()


def _sandbox_root() -> Path | None:
    raw = os.environ.get("HWPX_MCP_SANDBOX_ROOT")
    if raw is None:
        return None
    value = raw.strip()
    if not value:
        return None
    return Path(value).expanduser().resolve(strict=False)


def resolve_path(filename: str) -> str:
    candidate = Path(filename).expanduser()
    if candidate.is_absolute():
        resolved = candidate.resolve(strict=False)
    else:
        resolved = (Path.cwd() / candidate).resolve(strict=False)

    sandbox_root = _sandbox_root()
    if sandbox_root is not None:
        try:
            resolved.relative_to(sandbox_root)
        except ValueError as exc:
            raise PermissionError(f"path is outside sandbox root '{sandbox_root}': {filename}") from exc

    return str(resolved)


def truncate_response(text: str, max_chars: int = None) -> dict:
    if max_chars is None:
        max_chars = default_max_chars()
    total = len(text)
    if total <= max_chars:
        return {"text": text, "total_chars": total, "truncated": False}
    return {
        "text": text[:max_chars],
        "total_chars": total,
        "truncated": True,
    }
