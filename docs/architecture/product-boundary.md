# hwpx-mcp-server product boundary

`hwpx-mcp-server` is the application layer above `python-hwpx`. It turns
generic HWPX capabilities into office workflows without becoming the package or
OXML owner.

## MCP owns

- workflow, genre, profile, and office-policy services;
- typed application plans and deterministic orchestration;
- PII and compliance decisions;
- Hancom discovery and render-backend binding;
- workspace authorization and model-facing error contracts.

Application services live under `hwpx_mcp_server.office`. Handlers should
translate tool requests into these services rather than adding more business
logic to the core library.

## Dependency direction

MCP may import `hwpx`. It must not create or vendor a `hwpx` package and must
not import implementation from `hwpx-skill`. The core library must never import
MCP.

`office/rendering.py` is the canonical Hancom binding. Existing direct
`resolve_oracle` imports in `handlers/layout_style.py` and
`handlers/specialized.py` are frozen compatibility debt; new direct discovery
sites fail the product-boundary check.

Moving released core workflows here is a compatibility project, not a copy
project. Each move needs one owner, a primitive seam, a 4.x migration path, and
an explicit later removal gate.
