from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest


@dataclass(frozen=True)
class _ToolContext:
    target_path: Path
    secondary_path: Path
    seed_text: str
    find_text: str
    replace_text: str


def _tool_context_for(tmp_path: Path, *, seed: str, find_text: str, replace_text: str) -> _ToolContext:
    return _ToolContext(
        target_path=tmp_path / "contract_sample.hwpx",
        secondary_path=tmp_path / "contract_copy.hwpx",
        seed_text=seed,
        find_text=find_text,
        replace_text=replace_text,
    )


def test_initialize_request_and_response_contract(mcp_server_factory) -> None:
    client = mcp_server_factory(auto_initialize=False)

    payload = client.build_request_payload("initialize", client.default_initialize_params())
    assert payload["jsonrpc"] == "2.0"
    assert isinstance(payload["id"], int)
    assert payload["method"] == "initialize"
    assert isinstance(payload["params"], dict)

    request_id = client.send_payload(payload)
    response = client.wait_for_response(request_id)
    client._assert_response_envelope(response, expected_id=request_id)  # noqa: SLF001

    assert "error" not in response, client.debug_report()
    result = response["result"]
    assert isinstance(result, dict), client.debug_report()
    assert "protocolVersion" in result
    assert "capabilities" in result
    assert "serverInfo" in result

    # Some servers require this notification to fully complete initialization.
    client.notify("notifications/initialized", {})


def test_tools_list_contract(mcp_client) -> None:
    tools = mcp_client.list_tools()
    assert tools, f"tools/list returned no tools\n{mcp_client.debug_report()}"

    for tool in tools:
        assert isinstance(tool.get("name"), str) and tool["name"], f"invalid tool name: {tool}"
        input_schema = tool.get("inputSchema")
        assert isinstance(input_schema, dict), f"tool.inputSchema must be object: {tool}"
        properties = input_schema.get("properties", {})
        assert isinstance(properties, dict), f"tool.inputSchema.properties must be object: {tool}"


def test_tools_call_smoke_with_dynamic_tool_selection(mcp_server_factory, tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    client = mcp_server_factory(cwd=workspace)
    tools = client.list_tools()

    ctx = _tool_context_for(
        workspace,
        seed="MCP_CONTRACT_SEED_TEXT",
        find_text="MCP_CONTRACT_FIND",
        replace_text="MCP_CONTRACT_REPLACE",
    )

    create_tool = client.select_tool(tools, client.CREATE_HINTS)
    if create_tool is None:
        pytest.skip("No create-like tool found from tools/list")

    create_args = client.build_tool_arguments(create_tool, ctx)
    create_result = client.call_tool(create_tool["name"], create_args)
    assert bool(create_result.get("isError")) is False, client.debug_report()
    assert ctx.target_path.exists(), f"create-like tool did not create target file: {ctx.target_path}"

    add_tool = client.select_tool(
        tools,
        client.ADD_HINTS,
        exclude_keywords=("delete", "remove"),
    )
    smoke_tool = add_tool
    if smoke_tool is None:
        smoke_tool = client.select_tool(tools, client.READ_HINTS)
    if smoke_tool is None:
        pytest.skip("No additional smoke-callable tool found from tools/list")

    smoke_args = client.build_tool_arguments(smoke_tool, ctx)
    smoke_result = client.call_tool(smoke_tool["name"], smoke_args)
    assert bool(smoke_result.get("isError")) is False, client.debug_report()

    parsed = client.parse_tool_result_payload(smoke_result)
    assert parsed is None or isinstance(parsed, (dict, list)), (
        f"tool result payload parse failed for tool={smoke_tool['name']}: {smoke_result}"
    )


def test_invalid_jsonrpc_method_returns_error_object(mcp_client, mcp_test_config) -> None:
    response = mcp_client.request("tools/not_a_real_method", {})
    assert "result" not in response, f"invalid method unexpectedly returned success: {response}"
    assert "error" in response, f"invalid method must return error object: {response}"

    mcp_client.assert_error_object(response["error"], require_data=mcp_test_config.require_error_data)
