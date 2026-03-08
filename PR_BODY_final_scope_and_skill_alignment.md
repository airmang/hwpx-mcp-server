# Final Scope And Skill Alignment

## Summary

This pass performs a final consistency check after the scope pivot and the skill-first workflow work.

It does not add new public MCP tools.

Instead, it tightens the release-facing documentation so the repo consistently communicates:

- `python-hwpx` as the upstream engine layer
- `hwpx-mcp-server` as the MCP product surface
- skills and workflow docs as the orchestration layer

## What Was Clarified

- The authoritative public MCP surface is the FastMCP registration in `src/hwpx_mcp_server/server.py`.
- The current public tool count remains:
  - 30 tools in default mode
  - 40 tools with `HWPX_MCP_ADVANCED=1`
- The skill-first workflow docs and example skills are orchestration only and rely on the current active MCP tools.
- Legacy inventory and prompt/resource code still exists in the repo, but it is not the release-facing source of truth for the public surface.

## Documentation Tightening

- Updated `README.md` to make layer ownership explicit and to keep workflow guidance scoped to the active FastMCP surface.
- Updated `docs/use-cases.md` to reinforce the current workflow boundary and layer split.
- Updated `docs/upstream-audit.md` with the post-pivot layer ownership model and final consistency-pass notes.
- Updated `docs/follow-up-roadmap.md` with stronger release gates against doc drift and accidental public-surface growth.
- Added `docs/release-readiness-checklist.md` for release-time verification.

## Explicit Deferrals

The following remain intentionally deferred as public MCP tools:

- `validate_hwpx_package`
- `extract_reference_parts`
- `compare_reference_structure`
- `layout_drift_report`
- public `fill_template`
- public `save_as`

These either belong upstream in `python-hwpx`, belong in private helpers, or are better expressed as workflow orchestration over the existing MCP surface.

## Remaining Upstream Concerns

- Public style-creation support still belongs upstream.
- Engine-backed structure comparison primitives still belong upstream.
- Remaining private-header or XML-sensitive paths still need careful upstream adapter expansion.
- Validation must continue to use clean environments so a dirty sibling checkout does not become the release baseline.

## Testing

Suggested verification commands:

```bash
python -m pytest tests/test_mcp_end_to_end.py tests/test_advanced.py tests/test_contract.py -q
python C:\Users\kokyu\.codex\skills\.system\skill-creator\scripts\quick_validate.py examples\skills\reference-preserving-edit
python C:\Users\kokyu\.codex\skills\.system\skill-creator\scripts\quick_validate.py examples\skills\form-fill
python C:\Users\kokyu\.codex\skills\.system\skill-creator\scripts\quick_validate.py examples\skills\template-generation
```
