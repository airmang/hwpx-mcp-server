import pytest

from hwpx_mcp_server.tools import build_tool_definitions

BANNED_KEYS = {"$defs", "definitions", "$ref", "anyOf", "oneOf", "allOf", "if", "then", "else", "const"}


def _assert_object_shape(schema: dict) -> None:
    assert schema.get("type") == "object"
    assert "properties" in schema and isinstance(schema["properties"], dict)
    assert schema.get("additionalProperties") is False
    assert "required" in schema and isinstance(schema["required"], list)
    prop_keys = list(schema["properties"].keys())
    assert prop_keys == sorted(prop_keys)
    assert schema["required"] == sorted(schema["required"])


def _walk(node):
    if isinstance(node, dict):
        for key in BANNED_KEYS:
            assert key not in node
        type_value = node.get("type")
        if isinstance(type_value, list):
            assert "null" not in type_value
        if node.get("type") == "object" or "properties" in node:
            _assert_object_shape(node)
        for value in node.values():
            _walk(value)
    elif isinstance(node, list):
        for item in node:
            _walk(item)


@pytest.mark.parametrize("flag", ["0", "1"])
def test_tool_schemas_are_sanitized(monkeypatch, flag):
    monkeypatch.setenv("HWPX_MCP_HARDENING", flag)
    definitions = build_tool_definitions()
    for definition in definitions:
        tool = definition.to_tool()
        _walk(tool.inputSchema)
        _walk(tool.outputSchema)
        assert tool.inputSchema.get("type") == "object"
        assert tool.outputSchema.get("type") == "object"
