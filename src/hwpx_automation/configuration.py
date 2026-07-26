# SPDX-License-Identifier: Apache-2.0
"""Canonical automation configuration with bounded 6.x MCP fallbacks."""

from __future__ import annotations

import os

CANONICAL_ENV_PREFIX = "HWPX_AUTOMATION_"
LEGACY_ENV_PREFIX = "HWPX_MCP_"


def canonical_env_name(suffix: str) -> str:
    return f"{CANONICAL_ENV_PREFIX}{suffix}"


def legacy_env_name(suffix: str) -> str:
    return f"{LEGACY_ENV_PREFIX}{suffix}"


def env_value(suffix: str, default: str | None = None) -> str | None:
    """Read canonical configuration first, then the supported 6.x legacy key."""

    canonical = os.environ.get(canonical_env_name(suffix))
    if canonical is not None:
        return canonical
    legacy = os.environ.get(legacy_env_name(suffix))
    if legacy is not None:
        return legacy
    return default


def env_int(suffix: str, default: int) -> int:
    raw = env_value(suffix)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def env_float(suffix: str, default: float) -> float:
    raw = env_value(suffix)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


__all__ = [
    "CANONICAL_ENV_PREFIX",
    "LEGACY_ENV_PREFIX",
    "canonical_env_name",
    "env_float",
    "env_int",
    "env_value",
    "legacy_env_name",
]
