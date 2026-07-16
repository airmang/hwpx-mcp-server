from __future__ import annotations

import asyncio
from typing import Annotated, Any

import mcp.types as mcp_types
import pytest
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from pydantic import BaseModel, ConfigDict

from hwpx_mcp_server.fastmcp_adapter import (
    FastMcpAdapterError,
    describe_callables,
    normalize_schema,
    register_tool,
)


class _StructuredContent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str


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


def test_call_tool_result_requires_a_source_owned_structured_content_model() -> None:
    def bare_result() -> mcp_types.CallToolResult:
        return mcp_types.CallToolResult(content=[])

    with pytest.raises(
        FastMcpAdapterError,
        match=r"Annotated\[CallToolResult, OutputModel\]",
    ):
        describe_callables([("bare_result", bare_result, "bare", {})])


def test_annotated_call_tool_result_publishes_the_structured_content_model() -> None:
    def typed_result() -> Annotated[mcp_types.CallToolResult, _StructuredContent]:
        return mcp_types.CallToolResult(
            content=[mcp_types.TextContent(type="text", text="ok")],
            structuredContent={"status": "ok"},
        )

    snapshot = describe_callables(
        [("typed_result", typed_result, "typed", {})]
    )["typed_result"]

    assert snapshot.output_schema["title"] == "_StructuredContent"
    assert snapshot.output_schema["required"] == ["status"]
    assert "content" not in snapshot.output_schema["properties"]


def test_implicit_none_defaults_follow_the_declared_annotation() -> None:
    def legacy(value: str = None) -> dict:  # type: ignore[assignment]
        return {"value": value}

    schema = describe_callables([("legacy", legacy, "legacy", {})])[
        "legacy"
    ].input_schema["properties"]["value"]

    assert schema["type"] == "string"
    assert schema["default"] is None
    assert "anyOf" not in schema


def test_implicit_none_is_rejected_by_the_live_argument_model() -> None:
    def legacy(value: str = None) -> dict:  # type: ignore[assignment]
        return {"value": value}

    mcp = FastMCP("implicit-none-test")
    register_tool(mcp, name="legacy", func=legacy, description="legacy", meta={})

    with pytest.raises(ToolError, match="valid string"):
        asyncio.run(mcp.call_tool("legacy", {"value": None}))


def test_explicit_none_annotation_remains_nullable() -> None:
    def explicit(value: str | None = None) -> dict:
        return {"value": value}

    schema = describe_callables([("explicit", explicit, "explicit", {})])[
        "explicit"
    ].input_schema["properties"]["value"]

    assert {option.get("type") for option in schema["anyOf"]} == {"string", "null"}


def test_any_default_none_drops_redundant_null_union() -> None:
    def unrestricted(value: Any = None) -> dict:
        return {"value": value}

    schema = describe_callables(
        [("unrestricted", unrestricted, "unrestricted", {})]
    )["unrestricted"].input_schema["properties"]["value"]

    assert schema == {"default": None, "title": "Value"}
