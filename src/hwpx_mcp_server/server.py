"""MCP 서버 진입점(표준 입출력/Streamable HTTP transport 지원)."""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Dict, List, Mapping, Sequence

import anyio
import mcp.types as types
from mcp.server import NotificationOptions, Server
from mcp.shared.exceptions import McpError
from mcp.server.stdio import stdio_server
from mcp.server.streamable_http import StreamableHTTPServerTransport

from .hwpx_ops import (
    DEFAULT_PAGING_PARAGRAPH_LIMIT,
    HwpxOps,
    HwpxHandleNotFoundError,
    HwpxOperationError,
)
from .errors import mcp_code_for_error
from .logging_conf import configure_logging
from .prompts import get_prompt as render_prompt
from .prompts import list_prompts
from .storage import DocumentStorage, HttpDocumentStorage, LocalDocumentStorage
from .tools import ToolDefinition, build_tool_definitions

LOGGER = logging.getLogger(__name__)
DEFAULT_SERVER_NAME = "hwpx-mcp-server"



RESOURCE_URI_PATTERN = re.compile(r"^hwpx://documents/(?P<handle>[A-Za-z0-9_-]+)/(?P<kind>metadata|paragraphs|tables)$")
_RESOURCE_ERROR_HANDLE_NOT_FOUND = -32040


def _parse_resource_uri(uri: str) -> tuple[str, str]:
    match = RESOURCE_URI_PATTERN.match(uri.strip())
    if match is None:
        raise McpError(types.ErrorData(code=-32602, message=f"지원하지 않는 resource URI입니다: {uri}"))
    return match.group("handle"), match.group("kind")

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


def _build_server(ops: HwpxOps, tools: List[ToolDefinition]) -> Server:
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

    async def _list_resources(req: types.ListResourcesRequest | None) -> types.ServerResult:
        del req
        resources: list[types.Resource] = []
        for handle in ops.list_registered_handles():
            for suffix, label in (("metadata", "메타데이터"), ("paragraphs", "문단"), ("tables", "표")):
                uri = f"hwpx://documents/{handle.handle_id}/{suffix}"
                resources.append(
                    types.Resource(
                        name=f"{handle.handle_id}-{suffix}",
                        title=f"{handle.path} {label}",
                        uri=uri,
                        description=f"등록 handle {handle.handle_id}의 {label} 리소스",
                        mimeType="application/json",
                    )
                )
        result = types.ListResourcesResult(resources=resources, nextCursor=None)
        return types.ServerResult(result)

    async def _list_resource_templates(req: types.ListResourceTemplatesRequest | None) -> types.ServerResult:
        del req
        templates = [
            types.ResourceTemplate(
                name="document-metadata",
                title="문서 메타데이터",
                uriTemplate="hwpx://documents/{handle}/metadata",
                description="등록된 handle의 메타데이터 요약",
                mimeType="application/json",
            ),
            types.ResourceTemplate(
                name="document-paragraphs",
                title="문서 문단",
                uriTemplate="hwpx://documents/{handle}/paragraphs",
                description="등록된 handle의 전체 문단 텍스트",
                mimeType="application/json",
            ),
            types.ResourceTemplate(
                name="document-tables",
                title="문서 표",
                uriTemplate="hwpx://documents/{handle}/tables",
                description="등록된 handle의 표 구조 요약",
                mimeType="application/json",
            ),
        ]
        result = types.ListResourceTemplatesResult(resourceTemplates=templates, nextCursor=None)
        return types.ServerResult(result)

    async def _read_resource(req: types.ReadResourceRequest) -> types.ServerResult:
        params = req.params
        if params is None or params.uri is None:
            raise McpError(types.ErrorData(code=-32602, message="uri 파라미터가 필요합니다."))

        handle_id, kind = _parse_resource_uri(str(params.uri))

        try:
            if kind == "metadata":
                payload = ops.get_metadata_by_handle(handle_id)
            elif kind == "paragraphs":
                payload = ops.get_paragraphs_by_handle(handle_id)
            else:
                payload = ops.get_tables_by_handle(handle_id)
        except HwpxHandleNotFoundError as exc:
            raise McpError(
                types.ErrorData(
                    code=_RESOURCE_ERROR_HANDLE_NOT_FOUND,
                    message=str(exc),
                    data={"error": "HANDLE_NOT_FOUND", "handleId": handle_id},
                )
            ) from exc
        except HwpxOperationError as exc:
            payload = exc.to_payload()
            raise McpError(
                types.ErrorData(
                    code=mcp_code_for_error(exc.code),
                    message=exc.message,
                    data=payload,
                )
            ) from exc

        contents = [
            types.TextResourceContents(
                uri=str(params.uri),
                mimeType="application/json",
                text=json.dumps(payload, ensure_ascii=False, indent=2),
            )
        ]
        result = types.ReadResourceResult(contents=contents)
        return types.ServerResult(result)

    server.request_handlers[types.ListResourcesRequest] = _list_resources
    server.request_handlers[types.ListResourceTemplatesRequest] = _list_resource_templates
    server.request_handlers[types.ReadResourceRequest] = _read_resource

    async def _list_prompts(req: types.ListPromptsRequest | None) -> types.ServerResult:
        del req
        result = types.ListPromptsResult(prompts=list_prompts(), nextCursor=None)
        return types.ServerResult(result)

    async def _get_prompt(req: types.GetPromptRequest) -> types.ServerResult:
        params = req.params
        name = params.name if params else None
        if not name:
            raise McpError(types.ErrorData(code=-32602, message="name 파라미터가 필요합니다."))

        try:
            rendered = render_prompt(name, params.arguments if params else None)
        except KeyError as exc:
            raise McpError(
                types.ErrorData(code=-32602, message=f"지원하지 않는 prompt ID입니다: {name}")
            ) from exc
        except ValueError as exc:
            raise McpError(types.ErrorData(code=-32602, message=str(exc))) from exc

        return types.ServerResult(rendered)

    server.request_handlers[types.ListPromptsRequest] = _list_prompts
    server.request_handlers[types.GetPromptRequest] = _get_prompt

    @server.call_tool()
    async def _call_tool(name: str, arguments: Dict[str, object] | None) -> Dict[str, object]:
        definition = tool_map.get(name)
        if definition is None:
            raise ValueError(f"tool '{name}' is not registered")
        try:
            payload = definition.call(ops, arguments or {})
        except HwpxOperationError as exc:
            payload = exc.to_payload()
            raise McpError(
                types.ErrorData(
                    code=mcp_code_for_error(exc.code),
                    message=exc.message,
                    data=payload,
                )
            ) from exc
        except Exception as exc:  # pragma: no cover - defensive
            LOGGER.exception("tool '%s' failed", name)
            raise McpError(
                types.ErrorData(
                    code=-32000,
                    message=str(exc),
                    data={
                        "code": "INTERNAL_ERROR",
                        "message": str(exc),
                        "details": {"tool": name},
                    },
                )
            ) from exc
        return payload

    return server


async def _serve_stdio(server: Server) -> None:
    init_options = server.create_initialization_options(NotificationOptions())
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, init_options)


async def _serve_streamable_http(server: Server, *, host: str, port: int) -> None:
    import uvicorn

    init_options = server.create_initialization_options(NotificationOptions())
    transport = StreamableHTTPServerTransport(mcp_session_id=None)

    async def _app(scope, receive, send):
        await transport.handle_request(scope, receive, send)

    http_server = uvicorn.Server(
        uvicorn.Config(
            _app,
            host=host,
            port=port,
            log_level=os.getenv("LOG_LEVEL", "info").lower(),
        )
    )

    async with transport.connect() as (read_stream, write_stream):
        async with anyio.create_task_group() as task_group:
            task_group.start_soon(server.run, read_stream, write_stream, init_options)
            await http_server.serve()
            task_group.cancel_scope.cancel()


async def _serve(
    ops: HwpxOps,
    tools: List[ToolDefinition],
    *,
    transport: str,
    host: str,
    port: int,
) -> None:
    server = _build_server(ops, tools)
    if transport == "streamable-http":
        LOGGER.info("Starting MCP server over streamable HTTP", extra={"host": host, "port": port})
        await _serve_streamable_http(server, host=host, port=port)
        return

    await _serve_stdio(server)


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog=DEFAULT_SERVER_NAME)
    parser.add_argument(
        "--transport",
        choices=("stdio", "streamable-http"),
        default="stdio",
        help="MCP transport to use",
    )
    parser.add_argument(
        "--host",
        default=os.getenv("HWPX_MCP_HOST", "127.0.0.1"),
        help="Host interface used by streamable-http transport",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.getenv("HWPX_MCP_PORT", "8000")),
        help="TCP port used by streamable-http transport",
    )
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
        anyio.run(
            lambda: _serve(
                ops,
                tools,
                transport=args.transport,
                host=args.host,
                port=args.port,
            )
        )
    except KeyboardInterrupt:  # pragma: no cover - graceful shutdown
        LOGGER.info("Received interrupt, shutting down")
        return 130

    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
