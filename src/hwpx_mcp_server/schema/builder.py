"""Helpers for constructing sanitized tool schemas."""

from __future__ import annotations

from typing import Any, Dict, Type

from pydantic import BaseModel

from .sanitizer import SchemaSanitizer


def build_tool_schema(model: Type[BaseModel]) -> Dict[str, Any]:
    """Return a sanitized JSON schema for the provided model."""

    schema = model.model_json_schema(by_alias=True)
    if not isinstance(schema, dict):  # pragma: no cover - defensive guard
        raise TypeError("model_json_schema must return a mapping")
    sanitizer = SchemaSanitizer(schema)
    return sanitizer.sanitize()
