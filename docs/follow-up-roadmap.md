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

## 4. Phase 4 Scope Pivot

Default rule for the next pass:

- do not add new public MCP tools for reference-preserving workflows by default
- keep `python-hwpx` as the place for reusable engine/package semantics
- keep the current FastMCP tool list as the stable product surface

Candidate classification:

- `validate_hwpx_package`
  bucket: already covered by an existing MCP tool
  use `validate_structure` plus `package_parts`, `package_get_xml`, and `package_get_text` instead of adding a new alias
- `extract_reference_parts`
  bucket: better expressed as a workflow/skill instruction
  selecting "reference parts" is workflow-specific; document orchestration patterns using the existing package tools instead of expanding the public API
- `compare_reference_structure`
  bucket: better implemented upstream in `python-hwpx`
  if robust structure comparison becomes necessary, add reusable compare primitives upstream first and only then consider a downstream wrapper
- `layout_drift_report`
  bucket: better kept as an internal helper, not a public MCP tool
  any near-term drift reporting should remain private Python helper logic because the output is heuristic and not yet a stable product contract

Current verdict:

- none of these candidates currently qualifies as "truly worth exposing as a future public MCP tool"
- Phase 4 should start with workflow docs and existing-tool guidance, not with MCP surface growth

Gate for revisiting any public-tool proposal later:

- prove that the current MCP surface cannot support the workflow cleanly
- prove that the semantics are stable enough to deserve a versioned public contract
- prefer upstream `python-hwpx` support first when the capability depends on package or layout engine behavior

## 5. Documentation Hygiene

- Generate or script-check MCP tool inventories so README counts and names cannot drift again.
- Keep `docs/upstream-audit.md` updated whenever the upstream floor, editable checkout, or major integration path changes.
- Either align or clearly mark the legacy `legacy_server.py` / `tools.py` inventory as non-authoritative while `server.py` remains the product surface.
- Keep release-facing docs explicit that `server.py` is the current public MCP contract, while `tools.py`, `legacy_server.py`, and `prompts.py` are legacy until re-aligned.
- Convert remaining success-flag-only tests outside the Phase 1/2 core path (for example `add_page_break`) into persisted-output checks where the behavior matters.
- Replace stale test-count statements with command-based verification where practical.

## 6. Skill-First Workflow Layer

- Keep reference-preserving workflow examples thin and orchestration-focused under `docs/` and `examples/skills/`.
- Continue treating the active FastMCP tool inventory in `server.py` as the stable product surface.
- Do not add new public MCP tools for form-fill, template-generation, or reference-preserving review flows unless the current surface is proven insufficient.
- Prefer improving tool descriptions, workflow docs, and example skill order-of-operations before widening the server contract.
- Keep package/layout comparison semantics upstream-first; if reusable comparison primitives are needed, add them in `python-hwpx` before wrapping them here.
- Defer any prompt/resource revival until it can be registered against the active FastMCP surface without reintroducing legacy drift.

## 7. Release Alignment Gates

- Do not cut a release while docs still imply deferred public tools such as `fill_template`, structure diff, or layout-drift reporting on the active FastMCP surface, or while `save_as` docs lag behind its real response contract.
- Keep the layer split explicit in release docs:
  - `python-hwpx` = upstream engine
  - `hwpx-mcp-server` = MCP product surface
  - skills/workflows = orchestration layer
- Reconfirm the public tool count from `server.py` in both default and advanced mode before release notes are finalized.
