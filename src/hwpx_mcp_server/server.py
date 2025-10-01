"""표준 입력/출력 기반 MCP 서버의 진입점."""

from __future__ import annotations

import argparse
import logging
import os
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Dict, List, Mapping, Sequence

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
from .storage import DocumentStorage, HttpDocumentStorage, LocalDocumentStorage
from .tools import ToolDefinition, build_tool_definitions

LOGGER = logging.getLogger(__name__)
DEFAULT_SERVER_NAME = "hwpx-mcp-server"


def _bool_env(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _float_env(name: str) -> float | None:
    raw = os.getenv(name)
    if raw is None:
        return None
    try:
        return float(raw)
    except ValueError:
        LOGGER.warning("Invalid value for %s: expected float", name)
        return None


def _parse_header_assignments(assignments: Sequence[str]) -> Dict[str, str]:
    headers: Dict[str, str] = {}
    for assignment in assignments:
        item = assignment.strip()
        if not item:
            continue
        if "=" in item:
            key, value = item.split("=", 1)
        elif ":" in item:
            key, value = item.split(":", 1)
        else:
            LOGGER.warning("Ignoring malformed HTTP header assignment '%s'", item)
            continue
        headers[key.strip()] = value.strip()
    return headers


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


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog=DEFAULT_SERVER_NAME)
    parser.add_argument(
        "--storage",
        choices=("local", "http"),
        help="Storage backend to use (overrides HWPX_MCP_STORAGE)",
    )
    parser.add_argument(
        "--http-base-url",
        help="Base URL for the HTTP storage backend (overrides HWPX_MCP_HTTP_BASE_URL)",
    )
    parser.add_argument(
        "--http-timeout",
        type=float,
        help="Timeout in seconds for HTTP storage operations",
    )
    parser.add_argument(
        "--http-auth-token",
        help="Bearer token to send with HTTP storage requests (overrides HWPX_MCP_HTTP_AUTH_TOKEN)",
    )
    parser.add_argument(
        "--http-header",
        action="append",
        default=[],
        help=(
            "Additional HTTP header to send with storage requests. Format key=value or key:value. "
            "May be specified multiple times."
        ),
    )
    return parser.parse_args(argv)


def _select_storage(
    *,
    mode: str,
    base_directory: Path,
    auto_backup: bool,
    http_base_url: str | None,
    http_timeout: float | None,
    http_headers: Mapping[str, str] | None,
) -> DocumentStorage:
    if mode == "http":
        base_url = http_base_url or os.getenv("HWPX_MCP_HTTP_BASE_URL")
        if not base_url:
            raise ValueError("HTTP storage selected but no base URL provided")
        if auto_backup:
            LOGGER.info("Auto-backup is not supported for HTTP storage; ignoring flag")
        headers = dict(http_headers or {})
        timeout_value = http_timeout
        if timeout_value is None:
            timeout_value = _float_env("HWPX_MCP_HTTP_TIMEOUT")
        has_auth = any(key.lower() == "authorization" for key in headers)
        LOGGER.info(
            "Using HTTP storage backend",
            extra={
                "baseUrl": base_url,
                "headers": sorted(
                    key for key in headers if key.lower() != "authorization"
                ),
                "authorization": "provided" if has_auth else "absent",
            },
        )
        return HttpDocumentStorage(
            base_url,
            timeout=timeout_value,
            headers=headers,
            logger=LOGGER,
        )

    LOGGER.info(
        "Using current working directory for file operations",
        extra={"root": str(base_directory)},
    )
    return LocalDocumentStorage(
        base_directory=base_directory,
        auto_backup=auto_backup,
        logger=LOGGER,
    )


def main(argv: Sequence[str] | None = None) -> int:
    configure_logging(os.getenv("LOG_LEVEL"))

    args = _parse_args(argv)

    storage_mode = (args.storage or os.getenv("HWPX_MCP_STORAGE") or "local").strip().lower()
    if storage_mode not in {"local", "http"}:
        LOGGER.warning("Unknown storage mode '%s', falling back to local", storage_mode)
        storage_mode = "local"

    base_directory = Path.cwd()
    auto_backup = _bool_env("HWPX_MCP_AUTOBACKUP")

    paging_limit = os.getenv("HWPX_MCP_PAGING_PARA_LIMIT")
    try:
        paging_value = int(paging_limit) if paging_limit else DEFAULT_PAGING_PARAGRAPH_LIMIT
    except ValueError:
        LOGGER.warning(
            "Invalid HWPX_MCP_PAGING_PARA_LIMIT, falling back to %s",
            DEFAULT_PAGING_PARAGRAPH_LIMIT,
        )
        paging_value = DEFAULT_PAGING_PARAGRAPH_LIMIT

    header_tokens: List[str] = []
    env_header_raw = os.getenv("HWPX_MCP_HTTP_HEADERS")
    if env_header_raw:
        env_header_normalized = env_header_raw.replace(";", "\n")
        header_tokens.extend(env_header_normalized.splitlines())
    header_tokens.extend(args.http_header)
    http_headers = _parse_header_assignments(header_tokens)

    auth_token = args.http_auth_token or os.getenv("HWPX_MCP_HTTP_AUTH_TOKEN")
    if auth_token:
        http_headers.setdefault("Authorization", f"Bearer {auth_token.strip()}")

    storage = _select_storage(
        mode=storage_mode,
        base_directory=base_directory,
        auto_backup=auto_backup,
        http_base_url=args.http_base_url,
        http_timeout=args.http_timeout,
        http_headers=http_headers,
    )

    ops = HwpxOps(
        paging_paragraph_limit=paging_value,
        storage=storage,
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