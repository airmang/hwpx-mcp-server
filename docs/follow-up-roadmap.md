# Follow-up Roadmap

This file tracks work intentionally left out of Phase 1 / Phase 2.

## 1. Consolidate Upstream Adapters

- Move remaining `python-hwpx`-sensitive style/save helpers out of `hwpx_ops.py` and onto the shared compatibility helpers now used by `core/formatting.py`.
- Add explicit compatibility tests around any direct header XML mutation (`styles`, `fontfaces`, `charProperties`).
- Prefer one documented downstream adapter entrypoint per upstream concern:
  - style lookup/creation
  - run-range formatting
  - atomic persistence

## 2. Compatibility Matrix

- Test against at least:
  - lowest supported upstream line: `python-hwpx 2.6.x`
  - current validated upstream line: `python-hwpx 2.7.x`
- Record the matrix in CI and in release notes when the supported floor changes.

## 3. MCP Core Hardening

- Audit advanced `hwpx_ops.py` methods for direct `python-hwpx` assumptions and move repeated logic behind thin helpers.
- Add comments where downstream behavior depends on upstream XML layout rather than a public API.
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
- Replace stale test-count statements with command-based verification where practical.
