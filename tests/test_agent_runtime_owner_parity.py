# SPDX-License-Identifier: Apache-2.0
"""Parity between the MCP owner and core's frozen 4.x agent/blueprint/cli shape.

``hwpx.agent`` (and its ``blueprint``/``cli`` submodules) is scheduled for
physical deletion from core once python-hwpx is reduced to a library, so
this file imports nothing from it. Instead:

- Structural claims (exports, signatures, dataclass fields, the ``EXIT_*``
  constants) compare the live MCP module's
  ``tests.parity_fingerprint.fingerprint()`` against
  ``tests/parity_fingerprints/agent.json``, frozen from core while it still
  existed (see ``scripts/freeze_parity_fingerprints.py``).
- Behavioural claims that need an actual computed value — schema/catalog
  contents, command validation output, CLI stdout/stderr/exit codes — compare
  against ``tests/parity_fingerprints/agent.golden.json``: values captured
  from that same frozen core commit, confirmed deterministic (stable across
  repeat calls) and confirmed identical to MCP's own output at freeze time.

Every assertion the pre-freeze version of this file made is still made here;
none needed dropping.
"""
from __future__ import annotations

import io
import json
from collections.abc import Callable
from pathlib import Path

import pytest
from parity_fingerprint import fingerprint

import hwpx_mcp_server.office.agent as mcp_agent
import hwpx_mcp_server.office.agent.blueprint as mcp_blueprint
import hwpx_mcp_server.office.agent.cli as mcp_cli

_FIXTURES = Path(__file__).parent / "parity_fingerprints"
FROZEN = json.loads((_FIXTURES / "agent.json").read_text(encoding="utf-8"))["modules"]
GOLDEN = json.loads((_FIXTURES / "agent.golden.json").read_text(encoding="utf-8"))["calls"]


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


def test_root_blueprint_and_cli_shape_matches_frozen_core() -> None:
    assert fingerprint(mcp_agent) == FROZEN["hwpx.agent"]
    assert fingerprint(mcp_blueprint) == FROZEN["hwpx.agent.blueprint"]
    # Covers the EXIT_* constants too: they are simple int constants in
    # hwpx.agent.cli's frozen fingerprint, so a value drift here fails this
    # assertion instead of a separate constant-by-constant loop.
    assert fingerprint(mcp_cli) == FROZEN["hwpx.agent.cli"]


def test_generated_contracts_and_catalogs_match_frozen_core_output() -> None:
    assert mcp_agent.agent_contract_manifest() == GOLDEN["agentContractManifest"]
    assert mcp_agent.agent_catalog() == GOLDEN["agentCatalog"]
    assert mcp_agent.agent_json_schemas() == GOLDEN["agentJsonSchemas"]
    assert mcp_agent.mixed_form_json_schemas() == GOLDEN["mixedFormJsonSchemas"]
    assert mcp_blueprint.blueprint_catalog() == GOLDEN["blueprintCatalog"]
    assert mcp_blueprint.blueprint_json_schemas() == GOLDEN["blueprintJsonSchemas"]
    assert mcp_blueprint.blueprint_limits() == GOLDEN["blueprintLimits"]


def test_normalized_command_result_and_error_shapes_match_frozen_core_output() -> None:
    command = {
        "commandId": "set-title",
        "op": "set",
        "path": "/section[1]/paragraph[1]",
        "properties": {"text": "동결"},
    }
    assert mcp_agent.validate_agent_command(command) == GOLDEN["validateAgentCommand"]

    kwargs = {
        "code": "invalid_syntax",
        "message": "frozen",
        "target": "batch",
        "recoverability": "terminal",
        "suggestion": "retry",
        "valid_values": ("a", "b"),
    }
    assert mcp_agent.AgentError(**kwargs).to_dict() == GOLDEN["agentErrorToDict"]

    with pytest.raises(mcp_agent.AgentContractError) as caught:
        mcp_agent.validate_agent_batch({})
    payload = {
        "code": caught.value.code,
        "target": caught.value.target,
        "message": str(caught.value),
    }
    assert payload == GOLDEN["validateAgentBatchEmptyError"]


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
def test_cli_stdout_stderr_and_exit_codes_match_frozen_core_output(
    args: list[str],
) -> None:
    code, stdout, stderr = _run_cli(mcp_cli.main, args)
    expected = GOLDEN["cli"][" ".join(args)]
    assert {"exitCode": code, "stdout": stdout, "stderr": stderr} == expected


def test_cli_parser_prog_matches_frozen_core_output() -> None:
    assert mcp_cli.build_parser().prog == GOLDEN["cliParserProg"] == "hwpx"
