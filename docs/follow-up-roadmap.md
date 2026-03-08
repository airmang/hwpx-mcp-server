# Follow-up Roadmap

This file tracks work intentionally left out after the Phase 3 upstream-hardening pass.

## 1. Expand The Upstream Adapter Boundary

- Keep `src/hwpx_mcp_server/upstream.py` as the single downstream entrypoint for non-obvious `python-hwpx` calls.
- Move the remaining private-upstream paths behind it:
  - border-fill allocation in `hwpx_ops.py`
  - memo fallback XML writes in `core/content.py`
  - document bootstrapping in `hwp_converter.py`
- Add explicit compatibility tests around any direct header XML mutation (`styles`, `fontfaces`, `charProperties`, `borderFills`).

## 2. Compatibility Matrix

- Test against at least:
  - lowest supported upstream line: `python-hwpx 2.6.x`
  - current validated upstream line: `python-hwpx 2.7.x`
- Run that matrix in isolated virtualenvs so a dirty sibling editable checkout cannot shadow the intended upstream version.
- Record the matrix in CI and in release notes when the supported floor changes.

## 3. MCP Core Hardening

- Audit advanced `hwpx_ops.py` methods for any remaining direct `python-hwpx` assumptions and move repeated logic behind `upstream.py`.
- Add comments where downstream behavior still depends on upstream XML layout rather than a public API.
- Consider whether cache-sensitive direct XML mutations need a documented reopen cycle until upstream exposes explicit invalidation helpers.
- Consider exposing style IDs and char property IDs more consistently in tool responses where that helps future orchestration.

## 4. Phase 4 Tooling

Do not implement these in skills. Keep the business logic in Python/MCP.

- `validate_hwpx_package`
- `compare_reference_structure`
- `extract_reference_parts`
- `layout_drift_report`

For each tool:
- define the exact package/reference fidelity guarantees
- make heuristic limits explicit
- add regression fixtures before broadening docs

## 5. Documentation Hygiene

- Generate or script-check MCP tool inventories so README counts and names cannot drift again.
- Keep `docs/upstream-audit.md` updated whenever the upstream floor, editable checkout, or major integration path changes.
- Either align or clearly mark the legacy `legacy_server.py` / `tools.py` inventory as non-authoritative while `server.py` remains the product surface.
- Convert remaining success-flag-only tests outside the Phase 1/2 core path (for example `add_page_break`) into persisted-output checks where the behavior matters.
- Replace stale test-count statements with command-based verification where practical.
