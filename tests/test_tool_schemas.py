import jsonschema
import pytest

from hwpx_mcp_server.schema.builder import build_tool_schema
from hwpx_mcp_server.tools import OpenInfoOutput, build_tool_definitions

BANNED_KEYS = {"$defs", "definitions", "$ref", "anyOf", "oneOf", "allOf", "if", "then", "else", "const"}


def _assert_object_shape(schema: dict) -> None:
    assert schema.get("type") == "object"
    assert "properties" in schema and isinstance(schema["properties"], dict)
    assert "additionalProperties" in schema
    additional = schema["additionalProperties"]
    assert isinstance(additional, (bool, dict))
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


def test_open_info_meta_allows_additional_properties():
    schema = build_tool_schema(OpenInfoOutput)
    meta_schema = schema["properties"]["meta"]
    assert meta_schema["additionalProperties"]

    sample = {
        "meta": {
            "path": "document.hwpx",
            "absolutePath": "/tmp/document.hwpx",
            "size": 42,
            "modified": "2024-01-01T00:00:00",
        },
        "sectionCount": 3,
        "paragraphCount": 10,
        "headerCount": 1,
    }

    jsonschema.validate(sample, schema)


def test_convert_hwp_to_hwpx_tool_is_exposed():
    names = {definition.name for definition in build_tool_definitions()}
    assert "convert_hwp_to_hwpx" in names

def test_analyze_template_structure_tool_is_exposed():
    names = {definition.name for definition in build_tool_definitions()}
    assert "analyze_template_structure" in names


def test_document_handle_tools_are_exposed():
    names = {definition.name for definition in build_tool_definitions()}
    assert "open_document_handle" in names
    assert "list_open_documents" in names
    assert "close_document_handle" in names
    assert "copy_table_between_documents" in names
