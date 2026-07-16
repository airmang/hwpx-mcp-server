from __future__ import annotations

import pytest

from hwpx_mcp_server.fastmcp_adapter import FastMcpAdapterError, normalize_schema


def test_output_root_union_is_published_as_an_mcp_object_schema() -> None:
    schema = normalize_schema(
        {
            "$defs": {
                "First": {"type": "object"},
                "Second": {"type": "object"},
            },
            "anyOf": [
                {"$ref": "#/$defs/First"},
                {"$ref": "#/$defs/Second"},
            ],
        },
        input_schema=False,
    )

    assert schema["type"] == "object"
    assert len(schema["anyOf"]) == 2


def test_non_object_output_root_is_rejected_before_registration() -> None:
    with pytest.raises(
        FastMcpAdapterError,
        match="MCP output schema root must be object",
    ):
        normalize_schema({"type": "array", "items": {}}, input_schema=False)
