# SPDX-License-Identifier: Apache-2.0
"""Exact parity between the MCP owner and the frozen core 4.x copy."""
from __future__ import annotations

import inspect
import io
from collections.abc import Callable
from typing import Any

import pytest

import hwpx.agent as core_agent
import hwpx.agent.blueprint as core_blueprint
import hwpx.agent.cli as core_cli
import hwpx_mcp_server.office.agent as mcp_agent
import hwpx_mcp_server.office.agent.blueprint as mcp_blueprint
import hwpx_mcp_server.office.agent.cli as mcp_cli


def _assert_export_parity(left: Any, right: Any) -> None:
    assert list(left.__all__) == list(right.__all__)
    for name in left.__all__:
        left_value = getattr(left, name)
        right_value = getattr(right, name)
        assert type(left_value).__name__ == type(right_value).__name__, name
        if callable(left_value):
            assert str(inspect.signature(left_value)) == str(
                inspect.signature(right_value)
            ), name
        else:
            assert left_value == right_value, name


def _run_cli(
    main: Callable[..., int], args: list[str], stdin_text: str = ""
) -> tuple[int, str, str]:
    stdout = io.StringIO()
    stderr = io.StringIO()
    code = main(
        args,
        stdin=io.StringIO(stdin_text),
        stdout=stdout,
        stderr=stderr,
    )
    return code, stdout.getvalue(), stderr.getvalue()


def test_root_and_blueprint_public_exports_are_exact() -> None:
    _assert_export_parity(core_agent, mcp_agent)
    _assert_export_parity(core_blueprint, mcp_blueprint)


def test_generated_contracts_and_catalogs_are_exact() -> None:
    assert core_agent.agent_contract_manifest() == mcp_agent.agent_contract_manifest()
    assert core_agent.agent_catalog() == mcp_agent.agent_catalog()
    assert core_agent.agent_json_schemas() == mcp_agent.agent_json_schemas()
    assert core_agent.mixed_form_json_schemas() == mcp_agent.mixed_form_json_schemas()
    assert core_blueprint.blueprint_catalog() == mcp_blueprint.blueprint_catalog()
    assert (
        core_blueprint.blueprint_json_schemas()
        == mcp_blueprint.blueprint_json_schemas()
    )
    assert core_blueprint.blueprint_limits() == mcp_blueprint.blueprint_limits()


def test_normalized_command_result_and_error_shapes_are_exact() -> None:
    command = {
        "commandId": "set-title",
        "op": "set",
        "path": "/section[1]/paragraph[1]",
        "properties": {"text": "동결"},
    }
    assert core_agent.validate_agent_command(command) == mcp_agent.validate_agent_command(
        command
    )

    kwargs = {
        "code": "invalid_syntax",
        "message": "frozen",
        "target": "batch",
        "recoverability": "terminal",
        "suggestion": "retry",
        "valid_values": ("a", "b"),
    }
    assert core_agent.AgentError(**kwargs).to_dict() == mcp_agent.AgentError(
        **kwargs
    ).to_dict()

    for provider in (core_agent, mcp_agent):
        with pytest.raises(provider.AgentContractError) as caught:
            provider.validate_agent_batch({})
        payload = (caught.value.code, caught.value.target, str(caught.value))
        if provider is core_agent:
            core_payload = payload
        else:
            assert payload == core_payload


@pytest.mark.parametrize(
    "args",
    [
        ["--version"],
        ["--help"],
        ["help", "--json"],
        ["help", "blueprint", "--json"],
        ["view", "--help"],
        ["unknown-command"],
    ],
)
def test_cli_stdout_stderr_and_exit_codes_are_exact(args: list[str]) -> None:
    assert _run_cli(core_cli.main, args) == _run_cli(mcp_cli.main, args)


def test_cli_parser_and_exit_constants_are_exact() -> None:
    assert core_cli.build_parser().prog == mcp_cli.build_parser().prog == "hwpx"
    for name in (
        "EXIT_OK",
        "EXIT_UNEXPECTED",
        "EXIT_USAGE",
        "EXIT_TARGET",
        "EXIT_CONFLICT",
        "EXIT_VERIFICATION",
    ):
        assert getattr(core_cli, name) == getattr(mcp_cli, name)
