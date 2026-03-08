# Release Readiness Checklist

Use this checklist before tagging or publishing a release after the scope-pivot and skill-first workflow work.

## 1. Public Surface Contract

- Confirm the authoritative public MCP surface from `src/hwpx_mcp_server/server.py`.
- Recheck tool counts in both modes:
  - default mode
  - `HWPX_MCP_ADVANCED=1`
- Confirm no deferred tools were added to the active FastMCP surface:
  - `validate_hwpx_package`
  - `extract_reference_parts`
  - `compare_reference_structure`
  - `layout_drift_report`
  - `fill_template`
  - `save_as`

## 2. Documentation Alignment

- Confirm `README.md` describes the current FastMCP surface, not the legacy inventory.
- Confirm `docs/use-cases.md` points to workflow docs and does not imply new public tools.
- Confirm `docs/upstream-audit.md` still reflects:
  - `python-hwpx >= 2.6`
  - validated clean-venv runtime
  - active FastMCP surface counts
- Confirm `docs/follow-up-roadmap.md` still defers public-tool growth unless the existing surface is proven insufficient.
- Confirm any mention of legacy `tools.py`, `legacy_server.py`, or `prompts.py` is marked non-authoritative for release-facing docs.

## 3. Layer Ownership

- `python-hwpx` is described as the upstream engine layer.
- `hwpx-mcp-server` is described as the MCP product surface.
- Skills and workflow examples are described as orchestration only.
- No skill or workflow doc duplicates Python business logic from `python-hwpx`, `hwpx_ops.py`, or `core/`.

## 4. Skill and Workflow Examples

- Validate all example skills with the skill validator.
- Confirm example skills only rely on existing active MCP tools.
- Confirm limitations are stated honestly:
  - no public `fill_template`
  - no public `save_as`
  - `plan_edit` / `preview_edit` / `apply_edit` are review-pipeline tools, not a general patch engine

## 5. Upstream Compatibility

- Verify the release in a clean environment that does not import a dirty sibling checkout.
- Reconfirm the currently validated upstream release line.
- Record any remaining version-sensitive internals in release notes if they still depend on upstream private APIs.

## 6. Regression Checks

- Run the active-surface regression suite:

```bash
python -m pytest tests/test_mcp_end_to_end.py tests/test_advanced.py tests/test_contract.py -q
```

- Run the relevant formatting/style/save regressions when release scope touched the integration layer:

```bash
python -m pytest tests/test_formatting.py tests/test_hwpx_ops.py tests/test_http_storage.py -q
```

## 7. Release Notes

- State clearly what changed in docs/workflows versus what changed in code.
- Call out any explicitly deferred features instead of implying near-term availability.
- Summarize future upstream needs separately from downstream MCP roadmap items.
