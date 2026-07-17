# SPDX-License-Identifier: Apache-2.0
"""Typed process services shared by plain MCP handler callables.

The container owns mutable process configuration, while the canonical handler
callables and ToolSpec binding map remain immutable.  Handler modules depend on
this neutral container and never import the server facade or runtime composer.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, TYPE_CHECKING

from .hwpx_ops import HwpxOps

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP

    from .tool_contract import RegisteredToolRegistry


ToolBindings = Mapping[str, Callable[..., Any]]


@dataclass(slots=True)
class RuntimeServices:
    """One typed service graph for FastMCP and durable workflow dispatch."""

    ops: HwpxOps = field(default_factory=lambda: HwpxOps(auto_backup=False))
    idempotency_cache: dict[str, dict[str, Any]] = field(default_factory=dict)
    max_idempotency_cache_entries: int = 512
    active_advanced: bool = False
    mcp: FastMCP | None = None
    tool_registry: RegisteredToolRegistry | None = None
    tool_bindings: ToolBindings | None = None

    def configure_runtime(
        self,
        *,
        mcp: FastMCP,
        active_advanced: bool,
        tool_bindings: ToolBindings,
    ) -> None:
        """Bind transport and the one immutable callable map exactly once."""

        if self.mcp is not None and self.mcp is not mcp:
            raise RuntimeError("RuntimeServices FastMCP runtime is already bound")
        if self.tool_bindings is not None and self.tool_bindings is not tool_bindings:
            raise RuntimeError("RuntimeServices tool bindings are already bound")
        self.mcp = mcp
        self.active_advanced = active_advanced
        self.tool_bindings = tool_bindings

    def reconfigure_runtime(
        self,
        *,
        mcp: FastMCP,
        active_advanced: bool,
        tool_bindings: ToolBindings,
    ) -> None:
        """Replace only the import-time transport/profile composition.

        This preserves the released ``reload(server)`` environment-profile seam
        without replacing the singleton service graph referenced by handlers.
        """

        if self.tool_bindings is not None and self.tool_bindings is not tool_bindings:
            raise RuntimeError("RuntimeServices tool bindings are already bound")
        self.mcp = mcp
        self.active_advanced = active_advanced
        self.tool_bindings = tool_bindings
        self.tool_registry = None

    def install_registry(self, registry: RegisteredToolRegistry) -> None:
        if self.tool_registry is not None and self.tool_registry is not registry:
            if self.tool_registry.binding_hash() != registry.binding_hash():
                raise RuntimeError("RuntimeServices canonical registry is already bound")
        self.tool_registry = registry

    def replace_ops(self, ops: HwpxOps) -> None:
        """Replace the stable compatibility facade for an authorized workspace."""

        self.ops = ops

    def require_mcp(self) -> FastMCP:
        if self.mcp is None:
            raise RuntimeError("FastMCP runtime is not configured")
        return self.mcp

    def require_tool_bindings(self) -> ToolBindings:
        if self.tool_bindings is None:
            raise RuntimeError("canonical tool bindings are not configured")
        return self.tool_bindings


RUNTIME_SERVICES = RuntimeServices()


__all__ = ["RUNTIME_SERVICES", "RuntimeServices", "ToolBindings"]
