"""표준 입력/출력 기반 MCP 서버의 진입점."""

from __future__ import annotations

import logging
import os
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Dict, List

import anyio
import mcp.types as types
from mcp.server import NotificationOptions, Server
from mcp.server.stdio import stdio_server

from .hwpx_ops import (
    DEFAULT_PAGING_PARAGRAPH_LIMIT,
    HwpxOps,
    HwpxOperationError,
)
from .logging_conf import configure_logging
from .tools import ToolDefinition, build_tool_definitions

LOGGER = logging.getLogger(__name__)
DEFAULT_SERVER_NAME = "hwpx-mcp-server"


def _bool_env(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _resolve_version() -> str:
    try:
        return version("hwpx-mcp-server")
    except PackageNotFoundError:  # pragma: no cover - local development fallback
        return "0.0.0"


async def _serve(ops: HwpxOps, tools: List[ToolDefinition]) -> None:
    server = Server(DEFAULT_SERVER_NAME, version=_resolve_version())
    tool_map: Dict[str, ToolDefinition] = {tool.name: tool for tool in tools}
    cached_tools: List[types.Tool] | None = None

    async def _list_tools(req: types.ListToolsRequest | None) -> types.ServerResult:
        nonlocal cached_tools

        if cached_tools is None or len(cached_tools) != len(tools):
            cached_tools = [tool.to_tool() for tool in tools]
            server._tool_cache.clear()
            for tool in cached_tools:
                server._tool_cache[tool.name] = tool

        cursor_value = "0"
        if req is not None and req.params and req.params.cursor is not None:
            cursor_value = req.params.cursor

        try:
            start = int(cursor_value)
        except (TypeError, ValueError):
            start = 0

        if start < 0:
            start = 0

        total_tools = len(cached_tools)

        if start == 0:
            page_size = total_tools
        else:
            remaining = max(total_tools - start, 0)
            page_size = remaining
            if remaining and req is not None and req.params:
                limit = getattr(req.params, "limit", None)
                try:
                    parsed_limit = int(limit) if limit is not None else None
                except (TypeError, ValueError):
                    parsed_limit = None

                if parsed_limit is not None and parsed_limit > 0:
                    page_size = min(parsed_limit, remaining)

        end = min(start + page_size, total_tools)
        page_tools = cached_tools[start:end]
        next_cursor: str | None = None
        if end < len(cached_tools):
            next_cursor = str(end)

        result = types.ListToolsResult(tools=page_tools, nextCursor=next_cursor)
        return types.ServerResult(result)

    server.request_handlers[types.ListToolsRequest] = _list_tools

    @server.call_tool()
    async def _call_tool(name: str, arguments: Dict[str, object] | None) -> Dict[str, object]:
        definition = tool_map.get(name)
        if definition is None:
            raise ValueError(f"tool '{name}' is not registered")
        try:
            payload = definition.call(ops, arguments or {})
        except HwpxOperationError as exc:
            raise RuntimeError(str(exc)) from exc
        except Exception as exc:  # pragma: no cover - defensive
            LOGGER.exception("tool '%s' failed", name)
            raise RuntimeError(str(exc)) from exc
        return payload

    init_options = server.create_initialization_options(NotificationOptions())
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, init_options)


def main() -> int:
    configure_logging(os.getenv("LOG_LEVEL"))

    base_directory = Path.cwd()
    LOGGER.info(
        "Using current working directory for file operations",
        extra={"root": str(base_directory)},
    )

    paging_limit = os.getenv("HWPX_MCP_PAGING_PARA_LIMIT")
    try:
        paging_value = int(paging_limit) if paging_limit else DEFAULT_PAGING_PARAGRAPH_LIMIT
    except ValueError:
        LOGGER.warning(
            "Invalid HWPX_MCP_PAGING_PARA_LIMIT, falling back to %s",
            DEFAULT_PAGING_PARAGRAPH_LIMIT,
        )
        paging_value = DEFAULT_PAGING_PARAGRAPH_LIMIT

    ops = HwpxOps(
        base_directory=base_directory,
        paging_paragraph_limit=paging_value,
        auto_backup=_bool_env("HWPX_MCP_AUTOBACKUP"),
    )

    tools = build_tool_definitions()

    try:
        anyio.run(_serve, ops, tools)
    except KeyboardInterrupt:  # pragma: no cover - graceful shutdown
        LOGGER.info("Received interrupt, shutting down")
        return 130

    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())