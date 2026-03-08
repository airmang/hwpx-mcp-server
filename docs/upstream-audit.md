# Upstream Audit: `python-hwpx` Sync

Audit date: 2026-03-08

Inspected upstream sources:
- Active development runtime during audit: editable checkout from sibling repo `../python-hwpx`
- Clean validation runtime: released `python-hwpx 2.7.1` in an isolated virtualenv
- Local upstream tags present during audit: `v2.6`, `v2.7`, `v2.7.1`

## Version Truth

- Verified downstream behavior for this review: released `python-hwpx 2.7.1` in a clean virtualenv
- Active shell during code audit imported the sibling editable checkout, which was dirty and not a stable validation baseline
- Documented downstream floor after this sync: `python-hwpx >= 2.6`
- Reason for `2.6` floor:
  - this repo depends on upstream exporter APIs used by the current read/extract surface
  - local upstream history shows the exporter commit (`93413af`, "Add HWPX document exporters and integration tests") is contained in `v2.6+`
  - earlier downstream docs claiming `2.4` or `2.5` could not be justified from the audited upstream checkout

## Layer Ownership After Scope Pivot

- `python-hwpx` remains the upstream engine layer. Engine-level package semantics, structure comparison primitives, exporter behavior, and version-sensitive XML/layout handling belong there first.
- `hwpx-mcp-server` remains the downstream MCP product surface. The authoritative public contract is the FastMCP tool registration in `src/hwpx_mcp_server/server.py`.
- Workflow docs and example skills are orchestration only. They should compose the current MCP surface instead of duplicating engine logic or implying new public tools.
- `src/hwpx_mcp_server/legacy_server.py`, `src/hwpx_mcp_server/tools.py`, and `src/hwpx_mcp_server/prompts.py` still exist, but they are not the release-facing source of truth for the current MCP tool inventory.

## Upstream Dependency Touchpoints

| Downstream file | Upstream surface used | Notes |
|---|---|---|
| `src/hwpx_mcp_server/upstream.py` | `HwpxDocument.open()/new()`, `blank_document_bytes()`, `HwpxPackage.open()`, `TextExtractor`, `ObjectFinder`, `validate_document()`, exporters, header/style helpers | Phase 3 adapter boundary for non-obvious or version-sensitive upstream usage. |
| `src/hwpx_mcp_server/server.py` | payload parse/open flow, paragraph/table wrappers | Main MCP entrypoint; now relies on `upstream.py` for raw payload parsing instead of importing `HwpxDocument` directly. |
| `src/hwpx_mcp_server/core/document.py` | document open/create flow, `save_to_path()` via storage | Central local filesystem open/create/save path; direct open/template calls now route through `upstream.py`. |
| `src/hwpx_mcp_server/core/content.py` | `add_paragraph()`, `remove_paragraph()`, `add_table()`, `merge_cells()`, `split_merged_cell()`, `add_memo_with_anchor()`, `remove_memo()` | Mostly public upstream editing APIs. |
| `src/hwpx_mcp_server/core/formatting.py` | `doc.style()`, `doc.char_property()`, `header.ensure_char_property()`, run wrappers, header XML refs | Now delegates shared style/header logic to `upstream.py`; still version-sensitive because upstream has no public style creation API. |
| `src/hwpx_mcp_server/core/search.py` | paragraph/run traversal and text replacement behavior | Depends on upstream paragraph/run layout semantics. |
| `src/hwpx_mcp_server/storage.py` | document open validation, `save_to_path()` | Local atomic save now reopens through `upstream.py` so parser entrypoints are centralized. |
| `src/hwpx_mcp_server/hwpx_ops.py` | package/extractor/finder/validator/exporter helpers, run-style helpers, table/run helpers | High-density integration layer; Phase 3 moved duplicated upstream imports and char-style logic behind `upstream.py`. |
| `src/hwpx_mcp_server/hwp_converter.py` | `HwpxDocument.new()`, paragraph/table creation, save path | Conversion output must follow the same save semantics as the rest of the product. |
| `tests/` | direct `HwpxDocument` inspection | Regression coverage depends on inspecting real persisted HWPX output, not only return payloads. |

## Phase 3 Hardening Status

- Added `src/hwpx_mcp_server/upstream.py` as the downstream adapter boundary for `python-hwpx`.
- Centralized the following previously duplicated or scattered upstream touchpoints:
  - document open/new/template creation
  - package open
  - text extractor creation
  - object finder creation
  - structure validation
  - document exporters
  - private `_DEFAULT_CELL_WIDTH` lookup
  - shared char-style/style-id/style-element helpers
- `server.py`, `core/document.py`, `storage.py`, `core/formatting.py`, and `hwpx_ops.py` now consume that adapter instead of repeating direct upstream imports and helper logic.
- Regression tests were updated to patch adapter entrypoints (`create_text_extractor`) and to verify that `hwpx_ops` shares the same char-style integration path as `core/formatting.py`.

## Drift Found

### Version/documentation drift

- `pyproject.toml` previously required `python-hwpx >= 2.5`
- `README.md` previously claimed `python-hwpx >= 2.4`
- `CHANGELOG.md` previously claimed `2.5+`
- audited downstream feature set actually needs a documented floor of `2.6`

### Tool-surface drift

- `README.md` advertised MCP tools that are not exposed by `server.py`
  - stale: `get_tool_guide`, `export_text`, `export_html`, `export_markdown`
  - actual current read/extract MCP tools: `hwpx_to_markdown`, `hwpx_to_html`, `hwpx_extract_json`
- README/use-case tool counts were stale
  - actual inspected count: `30` basic tools
  - actual inspected count with `HWPX_MCP_ADVANCED=1`: `40` total tools

### Behavior/tests drift

- `format_text` split runs but kept the same `charPrIDRef`, so persisted output did not actually change formatting
- `create_custom_style` created a new style name but reused an arbitrary trailing style as the base and did not create a distinct upstream `charPr` when overrides were requested
- `add_paragraph(..., style="Name")` and `insert_paragraph(..., style="Name")` wrote unresolved style names into `styleIDRef` instead of resolving them to upstream style IDs
- local write paths mixed direct `save_to_path()` calls with storage-backed atomic writes
- prior tests for formatting/custom styles mainly asserted success flags or style counts, not the saved HWPX state

## Risky Integration Points

- Style authoring still depends on direct header XML mutation via `_styles_element()`, now isolated in `upstream.document_styles_element()`, because `python-hwpx` does not yet expose a public style-creation API.
- Shared char-style creation still depends on header-level `ensure_char_property()` hooks, now isolated in `upstream.ensure_char_style()`.
- Font family overrides require editing `<hh:fontfaces>` buckets directly; upstream schema/layout changes there would affect downstream style creation.
- Border-fill creation in `hwpx_ops.py` still depends on private header helpers such as `_allocate_border_fill_id()` and `_update_border_fills_item_count()`.
- `core/content.py` memo fallback still writes raw section XML when the native upstream memo call fails on mixed ET/lxml parents.
- Run-range formatting depends on splitting/cloning `HwpxOxmlRun` content while preserving nested `<hp:t>` fragments.
- Atomic save correctness depends on upstream `save_to_path()` continuing to produce fully reopenable packages before replacement.
- `hwp_converter.py` still constructs new documents directly through upstream instead of the Phase 3 adapter.
- The repo still carries an ET/lxml compatibility patch (`compat.py`) for mixed XML parent handling.
- Local developer environments can accidentally import a dirty sibling `python-hwpx` checkout and invalidate downstream test results unless validation is isolated.

## Recommended Fix Order

1. Align version truth first.
   - done in this phase by setting the downstream floor to `python-hwpx >= 2.6`
2. Make documented formatting behavior real.
   - done in this phase with persisted run-level `charPrIDRef` changes and output-verifying tests
3. Make style workflows usable end to end.
   - done in this phase by creating distinct char styles for custom styles and resolving style names to real upstream IDs
4. Unify write safety.
   - done in this phase by routing local writes through the shared atomic save path
5. Reduce future upgrade cost.
   - done in this phase by introducing `src/hwpx_mcp_server/upstream.py` and routing shared style/package/extractor/export/document-open logic through it
6. Continue shrinking the private-upstream surface.
   - follow-up: move border-fill creation, memo fallback XML writes, and converter bootstrapping onto the same adapter boundary where practical

## Required Follow-up Work

- Add CI coverage against at least one `python-hwpx 2.6.x` build and the current `2.7.x` line.
- Extend the adapter boundary to remaining private-upstream paths (`hwp_converter.py`, memo fallback XML, border-fill allocation helpers).
- Keep MCP docs generated from the actual registered tool list to avoid future tool-surface drift.
- Either align or explicitly retire the legacy `legacy_server.py` / `tools.py` tool inventory so stale tool descriptions cannot be mistaken for the active FastMCP surface.
- Keep Phase 4 scope narrowed: do not add new public MCP tools unless the current product surface is proven insufficient.
- Record future reference-structure engine needs in `python-hwpx` before duplicating semantics in the MCP layer.
- Watch upstream for a public style-creation API; replace direct `_styles_element()` mutation when that exists.

## Phase 4 Scope Pivot

Default direction after the Phase 3 hardening pass:

- `python-hwpx` remains the engine layer for package/structure semantics.
- the existing FastMCP surface remains the stable product API.
- reference-preserving workflows are mostly orchestration concerns unless a missing engine capability is clearly proven.

| Proposed tool | Bucket | Decision | Existing coverage / follow-up |
|---|---|---|---|
| `validate_hwpx_package` | already covered by an existing MCP tool | Do not add a new public alias. | Use `validate_structure` for package/schema validation, and combine `package_parts`, `package_get_xml`, and `package_get_text` when a workflow needs to inspect specific package parts. |
| `extract_reference_parts` | better expressed as a workflow/skill instruction | Do not add a public MCP tool. | "Reference parts" depends on the template/workflow context. Orchestration can already select and fetch the relevant parts with `package_parts`, `package_get_xml`, `package_get_text`, and `copy_document`. |
| `compare_reference_structure` | better implemented upstream in `python-hwpx` | Do not implement downstream first. | A trustworthy structure diff needs reusable engine semantics for manifests, package relationships, and normalization. If this becomes important, add comparison primitives upstream and consume them downstream later. |
| `layout_drift_report` | better kept as an internal helper, not a public MCP tool | Do not expose a stable public contract for heuristic layout drift yet. | Any near-term reporting should stay private Python helper logic, feeding orchestration or human review, until there is an engine-backed and testable model for layout comparison. |

Current conclusion:

- none of the four proposed Phase 4 candidates currently belongs in the "truly worth exposing as a future public MCP tool" bucket
- the near-term downstream work should focus on better docs and orchestration patterns around the existing MCP surface, not on widening that surface

## Final Consistency Pass

- Re-scanned the active FastMCP registration and confirmed the public surface remains `30` default tools and `40` tools with `HWPX_MCP_ADVANCED=1`.
- No deferred Phase 4 tools were added to `server.py`.
- Remaining surface-expansion risk is documentation drift from legacy inventories in `tools.py` and `legacy_server.py`, not from the active FastMCP server contract itself.
- Skill examples stay on the orchestration side of the boundary: they call existing MCP tools only and do not restate Python-side business logic from `hwpx_ops.py`, `core/`, or `python-hwpx`.
