# SPDX-License-Identifier: Apache-2.0
"""get_document_plan_schema MCP tool (rhwp T2 item 6)."""

from __future__ import annotations

from hwpx_mcp_server import server


def test_get_document_plan_schema_tool_returns_contract() -> None:
    schema = server.get_document_plan_schema()
    assert isinstance(schema, dict)
    assert schema["type"] == "object"
    assert set(schema["required"]) == {"schemaVersion", "blocks"}
    # block type enum is present (the Structured-Outputs constraint)
    assert schema["properties"]["blocks"]["items"]["properties"]["type"]["enum"]
    assert schema["$id"].endswith("document_plan.schema.json")
