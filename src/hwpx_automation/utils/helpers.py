# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

from ..configuration import env_int
from ..workspace import WorkspaceResolver


def default_max_chars() -> int:
    return max(1, env_int("MAX_CHARS", 10000))


MAX_CHARS = default_max_chars()


def resolve_path(filename: str) -> str:
    return str(WorkspaceResolver.from_environment().resolve(filename, must_exist=False))


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
