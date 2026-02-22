from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import zipfile

import pytest


@dataclass(frozen=True)
class _ToolContext:
    target_path: Path
    secondary_path: Path
    seed_text: str
    find_text: str
    replace_text: str


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _is_path_like_key(key: str) -> bool:
    lowered = key.lower()
    return any(token in lowered for token in ("filename", "path", "uri", "document"))


def _inject_path_argument(tool: dict, args: dict, path_value: str) -> dict:
    updated = dict(args)
    has_path_field = False
    for key in list(updated):
        if _is_path_like_key(key):
            updated[key] = path_value
            has_path_field = True
    if has_path_field:
        return updated

    schema = tool.get("inputSchema", {})
    properties = schema.get("properties", {}) if isinstance(schema, dict) else {}
    if isinstance(properties, dict):
        for key in properties:
            if _is_path_like_key(key):
                updated[key] = path_value
                has_path_field = True
                break
    if not has_path_field:
        pytest.skip(f"Tool has no path-like argument: {tool.get('name')}")
    return updated


def _copy_or_create_sample(
    *,
    client,
    tools: list[dict],
    sample_fixture_path: Path | None,
    target_path: Path,
) -> None:
    if sample_fixture_path and sample_fixture_path.exists():
        shutil.copy2(sample_fixture_path, target_path)
        return

    create_tool = client.select_tool(tools, client.CREATE_HINTS)
    if create_tool is None:
        pytest.skip("No fixture sample.hwpx and no create-like tool for fallback")

    ctx = _ToolContext(
        target_path=target_path,
        secondary_path=target_path.with_name("copy.hwpx"),
        seed_text="MCP_SAMPLE_BOOTSTRAP",
        find_text="MCP_SAMPLE_BOOTSTRAP",
        replace_text="MCP_SAMPLE_BOOTSTRAP",
    )
    args = client.build_tool_arguments(create_tool, ctx)
    client.call_tool(create_tool["name"], args)


def _extract_xml_parts(path: Path) -> list[tuple[str, str]]:
    if not zipfile.is_zipfile(path):
        return []
    parts: list[tuple[str, str]] = []
    with zipfile.ZipFile(path) as archive:
        for name in archive.namelist():
            if not name.lower().endswith(".xml"):
                continue
            try:
                payload = archive.read(name).decode("utf-8", errors="ignore")
            except Exception:  # pragma: no cover - corrupt zip edge case
                continue
            parts.append((name, payload))
    return parts


def _snapshot_for_marker(xml_parts: list[tuple[str, str]], marker: str) -> str:
    for name, text in xml_parts:
        index = text.find(marker)
        if index < 0:
            continue
        start = max(0, index - 160)
        end = min(len(text), index + len(marker) + 160)
        return f"{name}\n{text[start:end]}"
    preview = [f"{name}:len={len(text)}" for name, text in xml_parts[:8]]
    return "\n".join(preview)


def _assert_optional_golden(snapshot: str) -> None:
    golden_env = os.getenv("MCP_GOLDEN_SNAPSHOT_PATH")
    if not golden_env:
        return
    golden_path = Path(golden_env).expanduser()
    update = os.getenv("MCP_UPDATE_GOLDEN", "").strip().lower() in {"1", "true", "yes", "on"}
    if update or not golden_path.exists():
        golden_path.parent.mkdir(parents=True, exist_ok=True)
        golden_path.write_text(snapshot, encoding="utf-8")
        return
    expected = golden_path.read_text(encoding="utf-8")
    assert snapshot == expected, f"golden snapshot mismatch: {golden_path}"


def _assert_failed_tool_call(client, response: dict, mcp_test_config, *, context: str) -> None:
    if "error" in response:
        client.assert_error_object(response["error"], require_data=mcp_test_config.require_error_data)
        return

    result = response.get("result")
    assert isinstance(result, dict), f"{context}: unexpected envelope {response}"
    if mcp_test_config.strict_error_mode:
        pytest.fail(
            f"{context}: strict mode requires JSON-RPC error object, "
            f"but received successful envelope: {json.dumps(result, ensure_ascii=False)}"
        )
    assert bool(result.get("isError")) is True, (
        f"{context}: lenient mode expected result.isError=true when JSON-RPC error is absent: {result}"
    )


def test_file_edit_e2e_updates_real_file(
    mcp_server_factory,
    sample_hwpx_fixture_path: Path | None,
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    client = mcp_server_factory(cwd=workspace)
    tools = client.list_tools()

    sample_path = workspace / "sample.hwpx"
    _copy_or_create_sample(client=client, tools=tools, sample_fixture_path=sample_hwpx_fixture_path, target_path=sample_path)
    assert sample_path.exists(), f"sample file missing: {sample_path}"

    before_hash = _sha256(sample_path)
    marker = "MCP_E2E_MARKER_20260222"

    ctx = _ToolContext(
        target_path=sample_path,
        secondary_path=workspace / "sample_copy.hwpx",
        seed_text=marker,
        find_text="MCP_E2E_FIND_SOURCE",
        replace_text=marker,
    )

    add_tool = client.select_tool(tools, client.ADD_HINTS, exclude_keywords=("delete", "remove"))
    replace_tool = client.select_tool(tools, client.REPLACE_HINTS)

    if add_tool is not None:
        add_args = client.build_tool_arguments(add_tool, ctx)
        client.call_tool(add_tool["name"], add_args)
    elif replace_tool is not None:
        # If replace is the only available edit path, ensure a seed token exists first.
        seed_tool = client.select_tool(tools, client.ADD_HINTS, exclude_keywords=("delete", "remove"))
        if seed_tool is not None:
            seed_ctx = _ToolContext(
                target_path=sample_path,
                secondary_path=workspace / "sample_copy.hwpx",
                seed_text=ctx.find_text,
                find_text=ctx.find_text,
                replace_text=ctx.replace_text,
            )
            client.call_tool(seed_tool["name"], client.build_tool_arguments(seed_tool, seed_ctx))
        else:
            # Fixture documents usually contain this token; env can override it.
            ctx = _ToolContext(
                target_path=sample_path,
                secondary_path=workspace / "sample_copy.hwpx",
                seed_text=marker,
                find_text=os.getenv("MCP_REPLACE_FIND_TEXT", "HWPX"),
                replace_text=marker,
            )
        replace_args = client.build_tool_arguments(replace_tool, ctx)
        client.call_tool(replace_tool["name"], replace_args)
    else:
        pytest.fail(
            "No edit-capable tool found. Expected at least one add/insert/replace/edit-like tool in tools/list."
        )

    after_hash = _sha256(sample_path)
    assert after_hash != before_hash, (
        "Document hash did not change after edit call. "
        f"Tool call may not have persisted edits.\n{client.debug_report()}"
    )

    xml_parts = _extract_xml_parts(sample_path)
    assert xml_parts, f"Edited sample is not a valid HWPX(zip+xml) package: {sample_path}"

    marker_found = any(marker in xml for _, xml in xml_parts)
    read_tool = client.select_tool(tools, client.READ_HINTS, exclude_keywords=("list",))
    if read_tool is not None:
        read_ctx = _ToolContext(
            target_path=sample_path,
            secondary_path=workspace / "sample_copy.hwpx",
            seed_text=marker,
            find_text=marker,
            replace_text=marker,
        )
        read_args = client.build_tool_arguments(read_tool, read_ctx)
        read_result = client.call_tool(read_tool["name"], read_args)
        parsed = client.parse_tool_result_payload(read_result)
        if parsed is not None:
            marker_found = marker_found or (marker in json.dumps(parsed, ensure_ascii=False))

    assert marker_found, (
        "Edit marker not found in resulting document text/xml. "
        f"Expected marker={marker}\n{client.debug_report()}"
    )

    _assert_optional_golden(_snapshot_for_marker(xml_parts, marker))


def test_path_traversal_outside_workspace_is_blocked(
    mcp_server_factory,
    mcp_test_config,
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "sandbox"
    outside = tmp_path / "outside"
    workspace.mkdir()
    outside.mkdir()

    client = mcp_server_factory(cwd=workspace)
    tools = client.list_tools()

    target_tool = client.select_tool(tools, client.CREATE_HINTS)
    if target_tool is None:
        target_tool = client.select_tool(tools, client.READ_HINTS, exclude_keywords=("list",))
    if target_tool is None:
        pytest.skip("No path-accepting tool available for traversal security test")

    traversal_rel = Path("..") / "outside" / "escape.hwpx"
    outside_target = outside / "escape.hwpx"

    ctx = _ToolContext(
        target_path=workspace / "placeholder.hwpx",
        secondary_path=workspace / "placeholder_copy.hwpx",
        seed_text="MCP_TRAVERSAL",
        find_text="MCP_TRAVERSAL",
        replace_text="MCP_TRAVERSAL",
    )
    args = client.build_tool_arguments(target_tool, ctx)
    args = _inject_path_argument(target_tool, args, str(traversal_rel))

    response = client.call_tool_raw(target_tool["name"], args)
    if mcp_test_config.expect_path_sandbox:
        _assert_failed_tool_call(
            client,
            response,
            mcp_test_config,
            context=f"path traversal check tool={target_tool['name']}",
        )
        assert not outside_target.exists(), (
            "Path traversal was expected to be blocked but target file was created outside sandbox: "
            f"{outside_target}"
        )
    else:
        # Non-sandbox servers can opt out via MCP_EXPECT_PATH_SANDBOX=0.
        return


@pytest.mark.parametrize(
    ("case_name", "file_setup"),
    [
        ("broken_xml", lambda path: path.write_text("<xml><broken>", encoding="utf-8")),
        ("empty_file", lambda path: path.write_bytes(b"")),
    ],
)
def test_invalid_hwpx_input_returns_jsonrpc_error(
    mcp_server_factory,
    mcp_test_config,
    tmp_path: Path,
    case_name: str,
    file_setup,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    client = mcp_server_factory(cwd=workspace)
    tools = client.list_tools()
    read_tool = client.select_tool(tools, client.READ_HINTS, exclude_keywords=("list",))
    if read_tool is None:
        pytest.skip("No read-like tool available for invalid-input error contract test")

    bad_path = workspace / f"{case_name}.hwpx"
    file_setup(bad_path)

    ctx = _ToolContext(
        target_path=bad_path,
        secondary_path=workspace / "copy.hwpx",
        seed_text="MCP_BAD_INPUT",
        find_text="MCP_BAD_INPUT",
        replace_text="MCP_BAD_INPUT",
    )
    args = client.build_tool_arguments(read_tool, ctx)
    args = _inject_path_argument(read_tool, args, str(bad_path))

    response = client.call_tool_raw(read_tool["name"], args)
    _assert_failed_tool_call(
        client,
        response,
        mcp_test_config,
        context=f"invalid input case={case_name}, tool={read_tool['name']}",
    )


@pytest.mark.skipif(os.name == "nt", reason="chmod(0) permission denial test is POSIX-oriented")
def test_permission_denied_returns_jsonrpc_error(
    mcp_server_factory,
    mcp_test_config,
    sample_hwpx_fixture_path: Path | None,
    tmp_path: Path,
) -> None:
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        pytest.skip("permission denied test is unreliable when running as root")

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    client = mcp_server_factory(cwd=workspace)
    tools = client.list_tools()

    read_tool = client.select_tool(tools, client.READ_HINTS, exclude_keywords=("list",))
    if read_tool is None:
        pytest.skip("No read-like tool available for permission-denied test")

    restricted = workspace / "no_read_permission.hwpx"
    if sample_hwpx_fixture_path and sample_hwpx_fixture_path.exists():
        shutil.copy2(sample_hwpx_fixture_path, restricted)
    else:
        restricted.write_bytes(b"not-a-valid-hwpx")

    restricted.chmod(0)
    try:
        ctx = _ToolContext(
            target_path=restricted,
            secondary_path=workspace / "copy.hwpx",
            seed_text="MCP_PERMISSION",
            find_text="MCP_PERMISSION",
            replace_text="MCP_PERMISSION",
        )
        args = client.build_tool_arguments(read_tool, ctx)
        args = _inject_path_argument(read_tool, args, str(restricted))
        response = client.call_tool_raw(read_tool["name"], args)
    finally:
        restricted.chmod(stat.S_IRUSR | stat.S_IWUSR)

    _assert_failed_tool_call(
        client,
        response,
        mcp_test_config,
        context=f"permission denied tool={read_tool['name']}",
    )
