from __future__ import annotations

import hashlib
from pathlib import Path

import anyio

from hwpx import HwpxDocument
from hwpx.agent import AGENT_BATCH_RESULT_SCHEMA, HwpxAgentDocument, agent_json_schemas
from hwpx_mcp_server import server


def _fixture(path: Path) -> None:
    with HwpxDocument.new() as document:
        first = document.sections[0].paragraphs[0]
        first.element.set("id", "101")
        first.text = "평가 계획"
        second = document.add_paragraph("평가 방법")
        second.element.set("id", "102")
        table = second.add_table(1, 2)
        table.element.set("id", "201")
        table.rows[0].cells[0].text = "항목"
        table.rows[0].cells[1].text = "내용"
        document.save_to_path(path)


def _revision(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _paragraph_path(path: Path, identity: str = "101") -> str:
    with HwpxAgentDocument.open(path) as document:
        return next(
            record.path
            for record in document.records
            if record.kind == "paragraph" and record.attributes.get("id") == identity
        )


def test_read_tools_return_shared_nodes_paths_and_document_state(tmp_path: Path) -> None:
    source = tmp_path / "input.hwpx"
    _fixture(source)
    revision = _revision(source)

    root = server.get_document_node(
        str(source),
        path="/",
        depth=2,
        expected_revision=revision,
    )
    assert root["kind"] == "document"
    assert root["revision"] == revision
    assert root["document_revision"] == revision
    assert root["children"][0]["path"] == "/section[1]"

    result = server.query_document_nodes(
        str(source),
        'paragraph:contains("평가")',
        limit=10,
        expected_revision=revision,
    )
    assert result["revision"] == revision
    assert [node["summary"]["text"] for node in result["nodes"]] == [
        "평가 계획",
        "평가 방법",
    ]


def test_read_tools_fail_with_shared_structured_errors(tmp_path: Path) -> None:
    missing = server.get_document_node(str(tmp_path / "missing.hwpx"))
    assert missing["ok"] is False
    assert missing["error"]["code"] == "not_found"
    assert missing["error"]["schemaVersion"] == "hwpx.agent-error/v1"

    source = tmp_path / "input.hwpx"
    _fixture(source)
    stale = server.query_document_nodes(
        str(source),
        "paragraph",
        expected_revision="sha256:" + "0" * 64,
    )
    assert stale["ok"] is False
    assert stale["error"]["code"] == "stale_revision"
    assert stale["error"]["recoverability"] == "retryable"


def test_atomic_apply_reuses_core_verification_revision_and_idempotency(tmp_path: Path) -> None:
    source = tmp_path / "input.hwpx"
    output = tmp_path / "output.hwpx"
    _fixture(source)
    paragraph = _paragraph_path(source)
    kwargs = {
        "filename": str(source),
        "output": str(output),
        "commands": [
            {
                "commandId": "set-heading",
                "op": "set",
                "path": paragraph,
                "properties": {"text": "MCP 수정", "alignment": "CENTER"},
            },
            {
                "commandId": "copy-heading",
                "op": "copy",
                "path": paragraph,
                "parent": "/section[1]",
            },
        ],
        "expected_revision": _revision(source),
        "idempotency_key": "agent-mcp-replay-1",
        "overwrite": False,
    }

    first = server.apply_document_commands(**kwargs)
    replay = server.apply_document_commands(**kwargs)

    assert first["schemaVersion"] == AGENT_BATCH_RESULT_SCHEMA
    assert first["ok"] is True and first["rolledBack"] is False
    assert first["verificationReport"]["savePipeline"]["ok"] is True
    assert first["verificationReport"]["openSafety"]["ok"] is True
    assert first["verificationReport"]["idempotency"]["replayed"] is False
    assert replay["ok"] is True
    assert replay["verificationReport"]["idempotency"]["replayed"] is True
    assert replay["documentRevision"] == first["documentRevision"]
    assert replay["document_revision"] == first["document_revision"]
    with HwpxAgentDocument.open(output) as document:
        texts = [
            record.summary.get("text")
            for record in document.records
            if record.kind == "paragraph"
        ]
    assert texts.count("MCP 수정") == 2


def test_apply_stale_and_capability_skew_fail_closed_without_output(
    tmp_path: Path, monkeypatch
) -> None:
    source = tmp_path / "input.hwpx"
    stale_output = tmp_path / "stale.hwpx"
    skew_output = tmp_path / "skew.hwpx"
    _fixture(source)
    paragraph = _paragraph_path(source)
    command = {
        "commandId": "set",
        "op": "set",
        "path": paragraph,
        "properties": {"text": "금지"},
    }
    stale = server.apply_document_commands(
        str(source),
        str(stale_output),
        [command],
        expected_revision="sha256:" + "0" * 64,
    )
    assert stale["ok"] is False and stale["rolledBack"] is True
    assert stale["error"]["code"] == "stale_revision"
    assert not stale_output.exists()

    def blocked() -> None:
        raise RuntimeError("capability skew")

    monkeypatch.setattr(server.quality_contract, "assert_write_capability", blocked)
    skew = server.apply_document_commands(str(source), str(skew_output), [command])
    assert skew["schemaVersion"] == AGENT_BATCH_RESULT_SCHEMA
    assert skew["ok"] is False and skew["rolledBack"] is True
    assert skew["error"]["code"] == "verification_failed"
    assert skew["verificationReport"]["boundary"] == "mcp-capability-and-locator"
    assert not skew_output.exists()


def test_dry_run_does_not_publish_and_uses_input_state(tmp_path: Path) -> None:
    source = tmp_path / "input.hwpx"
    output = tmp_path / "dry.hwpx"
    _fixture(source)
    result = server.apply_document_commands(
        str(source),
        str(output),
        [
            {
                "commandId": "set",
                "op": "set",
                "path": _paragraph_path(source),
                "properties": {"text": "미리보기"},
            }
        ],
        dry_run=True,
    )
    assert result["ok"] is True and result["dryRun"] is True
    assert result["document_revision"] == _revision(source)
    assert not output.exists()


def test_fastmcp_schema_and_help_are_generated_from_core_catalog() -> None:
    async def tools_by_name():
        return {tool.name: tool for tool in await server.mcp.list_tools()}

    tools = anyio.run(tools_by_name)
    command_schema = tools["apply_document_commands"].inputSchema["properties"]["commands"]
    assert command_schema["items"] == agent_json_schemas()["command"]
    assert command_schema["minItems"] == 1
    assert command_schema["maxItems"] == 100
    get_schema = tools["get_document_node"].inputSchema["properties"]
    query_schema = tools["query_document_nodes"].inputSchema["properties"]
    assert get_schema["depth"]["minimum"] == 0
    assert get_schema["depth"]["maximum"] == 8
    assert get_schema["child_limit"]["maximum"] == 200
    assert query_schema["selector"]["maxLength"] == 512
    assert query_schema["limit"]["maximum"] == 100
    assert "Shared catalog sha256:" in tools["get_document_node"].description
    assert ":contains" in tools["query_document_nodes"].description
    assert "set/add/remove/move/copy" in tools["apply_document_commands"].description


def test_agent_document_tools_execute_over_stdio_protocol(
    mcp_server_factory, tmp_path: Path
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    source = workspace / "input.hwpx"
    output = workspace / "output.hwpx"
    _fixture(source)
    client = mcp_server_factory(cwd=workspace)
    tools = {tool["name"]: tool for tool in client.list_tools()}

    assert {
        "get_document_node",
        "query_document_nodes",
        "apply_document_commands",
    } <= set(tools)
    root = client.parse_tool_result_payload(
        client.call_tool(
            "get_document_node",
            {"filename": "input.hwpx", "path": "/", "depth": 2},
        )
    )
    assert root["kind"] == "document"

    query = client.parse_tool_result_payload(
        client.call_tool(
            "query_document_nodes",
            {
                "filename": "input.hwpx",
                "selector": 'paragraph:contains("평가 계획")',
                "limit": 5,
            },
        )
    )
    target = query["nodes"][0]["path"]
    result = client.parse_tool_result_payload(
        client.call_tool(
            "apply_document_commands",
            {
                "filename": "input.hwpx",
                "output": "output.hwpx",
                "commands": [
                    {
                        "commandId": "set",
                        "op": "set",
                        "path": target,
                        "properties": {"text": "프로토콜 수정"},
                    }
                ],
                "expected_revision": root["revision"],
                "idempotency_key": "stdio-agent-document-1",
            },
        )
    )
    assert result["ok"] is True
    assert result["verificationReport"]["openSafety"]["ok"] is True
    assert output.exists()
    with HwpxAgentDocument.open(output) as document:
        assert document.resolve_record(target).summary["text"] == "프로토콜 수정"
