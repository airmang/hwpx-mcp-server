# Upstream Audit: `python-hwpx` Sync

Audit date: 2026-03-08

Inspected upstream sources:
- Installed package: `python-hwpx 2.7.1`
- Installed location: editable checkout from sibling repo `../python-hwpx`
- Local upstream tags present during audit: `v2.6`, `v2.7`, `v2.7.1`

## Version Truth

- Verified downstream runtime during audit: `python-hwpx 2.7.1`
- Documented downstream floor after this sync: `python-hwpx >= 2.6`
- Reason for `2.6` floor:
  - this repo depends on upstream exporter APIs used by the current read/extract surface
  - local upstream history shows the exporter commit (`93413af`, "Add HWPX document exporters and integration tests") is contained in `v2.6+`
  - earlier downstream docs claiming `2.4` or `2.5` could not be justified from the audited upstream checkout

## Upstream Dependency Touchpoints

| Downstream file | Upstream surface used | Notes |
|---|---|---|
| `src/hwpx_mcp_server/server.py` | `HwpxDocument`, paragraph/table wrappers, tool-facing open/save flow | Main MCP entrypoint; accuracy depends on core helpers staying aligned with upstream wrappers. |
| `src/hwpx_mcp_server/core/document.py` | `HwpxDocument.open()`, `blank_document_bytes()`, `save_to_path()` | Central local filesystem open/create/save path. |
| `src/hwpx_mcp_server/core/content.py` | `add_paragraph()`, `remove_paragraph()`, `add_table()`, `merge_cells()`, `split_merged_cell()`, `add_memo_with_anchor()`, `remove_memo()` | Mostly public upstream editing APIs. |
| `src/hwpx_mcp_server/core/formatting.py` | `doc.style()`, `doc.char_property()`, `header.ensure_char_property()`, run wrappers, header XML refs | Most version-sensitive area; mixes public helpers with direct header XML edits because upstream has no public style creation API. |
| `src/hwpx_mcp_server/core/search.py` | paragraph/run traversal and text replacement behavior | Depends on upstream paragraph/run layout semantics. |
| `src/hwpx_mcp_server/storage.py` | `HwpxDocument.open()`, `save_to_path()` | Local atomic save validates temp output by reopening it through upstream. |
| `src/hwpx_mcp_server/hwpx_ops.py` | `ObjectFinder`, `HwpxPackage`, `TextExtractor`, `validate_document`, `ensure_run_style()`, table/run helpers | High-density upstream integration layer. |
| `src/hwpx_mcp_server/hwp_converter.py` | `HwpxDocument.new()`, paragraph/table creation, save path | Conversion output must follow the same save semantics as the rest of the product. |
| `tests/` | direct `HwpxDocument` inspection | Regression coverage depends on inspecting real persisted HWPX output, not only return payloads. |

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

- Style authoring still depends on direct header XML mutation via `_styles_element()` because `python-hwpx` does not yet expose a public style-creation API.
- Font family overrides require editing `<hh:fontfaces>` buckets directly; upstream schema/layout changes there would affect downstream style creation.
- Run-range formatting depends on splitting/cloning `HwpxOxmlRun` content while preserving nested `<hp:t>` fragments.
- Atomic save correctness depends on upstream `save_to_path()` continuing to produce fully reopenable packages before replacement.
- The repo still carries an ET/lxml compatibility patch (`compat.py`) for mixed XML parent handling.

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
   - follow-up: move more `hwpx_ops.py` style/save logic onto the same shared adapter helpers used by `core/formatting.py`

## Required Follow-up Work

- Add CI coverage against at least one `python-hwpx 2.6.x` build and the current `2.7.x` line.
- Keep MCP docs generated from the actual registered tool list to avoid future tool-surface drift.
- Build the Phase 4 reference-preserving analysis tools on top of MCP/Python logic rather than skill-only instructions.
- Watch upstream for a public style-creation API; replace direct `_styles_element()` mutation when that exists.
