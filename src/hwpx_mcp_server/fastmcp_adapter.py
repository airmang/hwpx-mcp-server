# SPDX-License-Identifier: Apache-2.0
"""Narrow FastMCP compatibility seam for the canonical tool registry.

S-079 needs to inspect the callable and JSON schemas that FastMCP actually
registered.  FastMCP did not expose that information through a stable public
API across the supported SDK range, so all private-manager access lives here.
S-080 can replace this module without changing the release-facing registry.
"""

from __future__ import annotations

import copy
import functools
import inspect
from dataclasses import dataclass
from typing import Annotated, Any, Callable, Mapping, get_args, get_origin, get_type_hints

from mcp.server.fastmcp import FastMCP
from pydantic import TypeAdapter

from .execution_lock import PUBLIC_MUTATION_LOCK


class FastMcpAdapterError(RuntimeError):
    """FastMCP cannot provide the registry guarantees required by the server."""


@dataclass(frozen=True, slots=True)
class FastMcpToolSnapshot:
    name: str
    callable: Callable[..., Any]
    description: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    meta: dict[str, Any]


def normalize_schema(schema: Mapping[str, Any] | None, *, input_schema: bool) -> dict[str, Any]:
    """Return a deterministic JSON-compatible schema.

    Object inputs are closed at every generated object boundary unless their
    schema explicitly declares an open mapping. Array order is preserved so
    catalog-owned schemas remain byte-for-byte attributable to their source.
    MCP restricts both tool input and output schemas to an object at the root;
    Pydantic root-model unions need that discriminator restored explicitly.
    """

    def visit(value: Any) -> Any:
        if isinstance(value, Mapping):
            out = {str(key): visit(nested) for key, nested in sorted(value.items(), key=lambda item: str(item[0]))}
            # A ``properties`` member can itself be a user field name inside a
            # JSON Schema property map. Only explicit object-schema nodes are
            # eligible for generated closure defaults.
            if out.get("type") == "object":
                composed = any(
                    keyword in out for keyword in ("$ref", "allOf", "anyOf", "oneOf")
                )
                if "properties" in out or not composed:
                    out.setdefault("properties", {})
                    if "additionalProperties" not in out:
                        out["additionalProperties"] = (
                            False if out["properties"] else True
                        )
            return out
        if isinstance(value, (list, tuple)):
            return [visit(item) for item in value]
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        raise FastMcpAdapterError(f"schema contains non-JSON value: {type(value).__name__}")

    normalized = visit(dict(schema or {}))
    if not isinstance(normalized, dict):  # pragma: no cover - defensive
        raise FastMcpAdapterError("tool schema must be a JSON object")
    if input_schema:
        normalized.setdefault("type", "object")
        normalized.setdefault("properties", {})
        # A tool call is never an open mapping, including zero-argument tools.
        # Nested mapping values keep their explicitly generated openness.
        normalized["additionalProperties"] = False
        normalized.setdefault("required", [])
    else:
        root_type = normalized.get("type")
        if root_type not in (None, "object"):
            raise FastMcpAdapterError(
                f"MCP output schema root must be object, got {root_type!r}"
            )
        normalized.setdefault("type", "object")
        if len(normalized) == 1:
            normalized.update({"properties": {}, "additionalProperties": True})
    return normalized


def _resolved_return_annotation(func: Callable[..., Any]) -> Any:
    try:
        return get_type_hints(func, include_extras=True).get(
            "return", inspect.Signature.empty
        )
    except (NameError, TypeError):
        return inspect.signature(func).return_annotation


def _annotation_schema_extra(annotation: Any) -> dict[str, Any]:
    """Collect mapping-valued Field.json_schema_extra without reordering it."""

    extras: dict[str, Any] = {}
    while get_origin(annotation) is Annotated:
        nested, *metadata = get_args(annotation)
        for item in metadata:
            extra = getattr(item, "json_schema_extra", None)
            if isinstance(extra, Mapping):
                extras.update(copy.deepcopy(dict(extra)))
        annotation = nested
    return extras


def _restore_parameter_schema_extras(
    schema: Mapping[str, Any] | None,
    func: Callable[..., Any],
) -> dict[str, Any]:
    """Restore catalog-owned schema fragments that Pydantic may reorder.

    Pydantic faithfully carries ``json_schema_extra`` into the generated
    schema, but it normalizes nested ``required`` arrays. The shared agent
    catalog is the canonical byte-level source, so overlay the original extras
    after generation before storing the FastMCP registry receipt.
    """

    restored = copy.deepcopy(dict(schema or {}))
    properties = restored.get("properties")
    if not isinstance(properties, dict):
        return restored
    try:
        hints = get_type_hints(func, include_extras=True)
    except (NameError, TypeError):
        hints = dict(getattr(func, "__annotations__", {}))
    for name, annotation in hints.items():
        if name == "return" or name not in properties:
            continue
        extra = _annotation_schema_extra(annotation)
        if extra and isinstance(properties[name], dict):
            properties[name].update(extra)
    top_level_extra = getattr(func, "__hwpx_input_schema_extra__", None)
    if isinstance(top_level_extra, Mapping):
        restored.update(copy.deepcopy(dict(top_level_extra)))
    return restored


def _registration_callable(func: Callable[..., Any]) -> tuple[Callable[..., Any], bool]:
    """Normalize bare ``dict`` returns for structured output without altering API functions."""

    try:
        # ``include_extras`` is contract-critical: the core agent catalog binds
        # its closed command/blueprint schemas through Annotated[...,
        # Field(json_schema_extra=...)]. Stripping those extras would silently
        # widen FastMCP inputs to generic dicts.
        resolved_hints = get_type_hints(func, include_extras=True)
    except (NameError, TypeError):
        resolved_hints = dict(getattr(func, "__annotations__", {}))
    return_annotation = resolved_hints.get("return", inspect.signature(func).return_annotation)
    if return_annotation is not dict:
        return func, return_annotation is not inspect.Signature.empty and return_annotation is not Any

    @functools.wraps(func)
    def structured_wrapper(*args: Any, **kwargs: Any) -> dict[str, Any]:
        return func(*args, **kwargs)

    original_signature = inspect.signature(func)
    structured_wrapper.__signature__ = original_signature.replace(  # type: ignore[attr-defined]
        parameters=[
            parameter.replace(annotation=resolved_hints.get(parameter.name, parameter.annotation))
            for parameter in original_signature.parameters.values()
        ],
        return_annotation=dict[str, Any],
    )
    structured_wrapper.__annotations__ = {
        **resolved_hints,
        "return": dict[str, Any],
    }
    setattr(structured_wrapper, "__hwpx_original_callable__", func)
    return structured_wrapper, True


def _serialize_mutation_callable(
    func: Callable[..., Any],
    *,
    mutates: bool,
) -> Callable[..., Any]:
    """Serialize catalog-declared writers without changing their tool schema."""

    if not mutates:
        return func

    @functools.wraps(func)
    def serialized(*args: Any, **kwargs: Any) -> Any:
        with PUBLIC_MUTATION_LOCK:
            return func(*args, **kwargs)

    # The public server module uses postponed annotations.  A wrapper defined
    # in this adapter has a different globals namespace, so copying annotation
    # strings verbatim makes FastMCP try to resolve e.g. ``mcp_types`` here and
    # can incorrectly treat CallToolResult as structured output.  Bind the
    # already-resolved objects onto both the signature and annotations.
    try:
        resolved_hints = get_type_hints(func, include_extras=True)
    except (NameError, TypeError):
        resolved_hints = dict(getattr(func, "__annotations__", {}))
    original_signature = inspect.signature(func)
    serialized.__signature__ = original_signature.replace(  # type: ignore[attr-defined]
        parameters=[
            parameter.replace(
                annotation=resolved_hints.get(parameter.name, parameter.annotation)
            )
            for parameter in original_signature.parameters.values()
        ],
        return_annotation=resolved_hints.get(
            "return", original_signature.return_annotation
        ),
    )
    serialized.__annotations__ = resolved_hints
    setattr(serialized, "__hwpx_original_callable__", original_callable(func))
    return serialized


def original_callable(func: Callable[..., Any]) -> Callable[..., Any]:
    """Return the public server function behind an adapter-generated wrapper."""

    seen: set[int] = set()
    current = func
    while callable(current) and id(current) not in seen:
        seen.add(id(current))
        nested = getattr(current, "__hwpx_original_callable__", current)
        if nested is current:
            return current
        current = nested
    return current


def _manager_tools(mcp: Any) -> dict[str, Any]:
    manager = getattr(mcp, "_tool_manager", None)
    tools = getattr(manager, "_tools", None)
    if manager is None or not isinstance(tools, dict):
        raise FastMcpAdapterError(
            "FastMCP tool manager is unavailable; supported registry introspection is required"
        )
    return tools


def _register(
    mcp: Any,
    *,
    name: str,
    func: Callable[..., Any],
    description: str,
    meta: Mapping[str, Any],
) -> FastMcpToolSnapshot:
    adapted, structured_output = _registration_callable(func)
    adapted = _serialize_mutation_callable(
        adapted,
        mutates=bool(meta.get("hwpxMutates")),
    )
    tool_method = getattr(mcp, "tool", None)
    if not callable(tool_method):
        raise FastMcpAdapterError("FastMCP.tool is unavailable")
    parameters = inspect.signature(tool_method).parameters
    kwargs: dict[str, Any] = {"name": name}
    if "description" in parameters:
        kwargs["description"] = description
    if "meta" in parameters:
        kwargs["meta"] = dict(meta)
    if "structured_output" not in parameters:
        raise FastMcpAdapterError(
            "FastMCP.tool lacks structured_output; mcp>=1.14.1 is required"
        )
    kwargs["structured_output"] = structured_output
    tool_method(**kwargs)(adapted)

    tool = _manager_tools(mcp).get(name)
    if tool is None:
        raise FastMcpAdapterError(f"FastMCP did not retain registered tool {name!r}")
    input_json = normalize_schema(
        getattr(tool, "parameters", None), input_schema=True
    )
    # Overlay catalog fragments after normalization so their original array
    # order and intentional open-object semantics remain exact.
    input_json = _restore_parameter_schema_extras(input_json, func)
    raw_output = getattr(tool, "output_schema", None)
    if raw_output is None:
        return_annotation = _resolved_return_annotation(func)
        try:
            raw_output = TypeAdapter(return_annotation).json_schema()
        except Exception as exc:  # pragma: no cover - every public tool is annotated
            raise FastMcpAdapterError(f"cannot derive output schema for {name!r}: {exc}") from exc
    output_json = normalize_schema(raw_output, input_schema=False)

    # Make the live MCP schema byte-for-byte comparable with the canonical
    # registry.  Non-structured CallToolResult functions keep FastMCP's None
    # output schema because the SDK handles those result objects specially.
    tool.parameters = input_json
    if structured_output:
        tool.output_schema = output_json
    if hasattr(tool, "description"):
        tool.description = description
    if hasattr(tool, "meta") and "meta" in parameters:
        tool.meta = dict(meta)
    return FastMcpToolSnapshot(
        name=name,
        callable=original_callable(getattr(tool, "fn", adapted)),
        description=str(getattr(tool, "description", description) or ""),
        input_schema=input_json,
        output_schema=output_json,
        meta=dict(getattr(tool, "meta", None) or meta),
    )


def describe_callables(
    entries: list[tuple[str, Callable[..., Any], str, Mapping[str, Any]]],
) -> dict[str, FastMcpToolSnapshot]:
    """Build schemas for every public callable, including inactive advanced tools."""

    schema_server = FastMCP("hwpx-tool-contract-schema")
    snapshots: dict[str, FastMcpToolSnapshot] = {}
    for name, func, description, meta in entries:
        snapshots[name] = _register(
            schema_server,
            name=name,
            func=func,
            description=description,
            meta=meta,
        )
    return snapshots


def register_tool(
    mcp: Any,
    *,
    name: str,
    func: Callable[..., Any],
    description: str,
    meta: Mapping[str, Any],
) -> FastMcpToolSnapshot:
    return _register(mcp, name=name, func=func, description=description, meta=meta)


def registered_tool_snapshots(mcp: Any) -> dict[str, FastMcpToolSnapshot]:
    """Read the live registry, deriving effective output schema when necessary."""

    snapshots: dict[str, FastMcpToolSnapshot] = {}
    for name, tool in sorted(_manager_tools(mcp).items()):
        func = original_callable(getattr(tool, "fn", None))
        if not callable(func):
            raise FastMcpAdapterError(f"registered tool {name!r} has no callable")
        raw_output = getattr(tool, "output_schema", None)
        if raw_output is None:
            try:
                raw_output = TypeAdapter(_resolved_return_annotation(func)).json_schema()
            except Exception as exc:
                raise FastMcpAdapterError(f"cannot derive live output schema for {name!r}: {exc}") from exc
        input_schema = normalize_schema(
            getattr(tool, "parameters", None), input_schema=True
        )
        input_schema = _restore_parameter_schema_extras(input_schema, func)
        snapshots[name] = FastMcpToolSnapshot(
            name=name,
            callable=func,
            description=str(getattr(tool, "description", "") or ""),
            input_schema=input_schema,
            output_schema=normalize_schema(raw_output, input_schema=False),
            meta=dict(getattr(tool, "meta", None) or {}),
        )
    return snapshots


__all__ = [
    "FastMcpAdapterError",
    "FastMcpToolSnapshot",
    "describe_callables",
    "normalize_schema",
    "original_callable",
    "register_tool",
    "registered_tool_snapshots",
]
