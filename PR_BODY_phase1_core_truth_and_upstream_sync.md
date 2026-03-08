# Phase 1 / 2: Core Truth And Upstream Sync

## Summary

This PR handles Phase 1 and Phase 2 only.

- audits the downstream `python-hwpx` dependency boundary
- aligns the documented upstream version floor with the actual MCP feature set
- fixes formatting/custom-style behavior so persisted HWPX output matches the docs
- routes local writes through the shared atomic save path
- adds regression tests that inspect real saved document state

No new MCP tools are added in this PR.

## Why

`hwpx-mcp-server` is a downstream integration layer on top of `python-hwpx`, but the repo had drift in three places:

- docs claimed older upstream minimums than the current MCP feature set justified
- `format_text` and `create_custom_style` reported success without proving meaningful saved output changes
- local write paths mixed atomic and non-atomic save behavior

This PR makes the documented Phase 1/2 core behavior true before adding new MCP surface area.

## What Changed

### Upstream alignment

- audited installed upstream `python-hwpx 2.7.1` and the sibling editable checkout
- set the downstream documented/package floor to `python-hwpx >= 2.6`
- added [`docs/upstream-audit.md`](docs/upstream-audit.md)
- updated README / changelog / use-case docs to match the actual MCP tool surface and version truth

### Formatting and styles

- `format_text` now persists real run-level `charPrIDRef` updates
- run-range formatting now survives reopen by changing actual upstream char style references
- `create_custom_style` now returns a reusable `style_id`
- custom styles create a distinct upstream `charPr` when formatting overrides are requested
- `add_paragraph(..., style=...)` and `insert_paragraph(..., style=...)` now resolve style names to real upstream style IDs instead of writing unresolved names into `styleIDRef`

### Save safety

- local write paths now use the shared atomic save flow (`temp -> validate -> replace`)
- `core.document`, `hwp_converter`, and remaining direct-save `hwpx_ops` paths were aligned to the storage-backed persistence path

### Tests

- added persisted-output tests for:
  - run-level formatting changes
  - custom style creation + application by style name
  - custom style id reuse
  - atomic save failure preserving the original file

## Tests Run

Focused regression pass:

```bash
python -m pytest -q tests/test_formatting.py tests/test_http_storage.py tests/test_hwpx_ops.py::test_save_as_creates_new_file tests/test_hwpx_ops.py::test_fill_template_replaces_multiple_tokens_without_modifying_source
```

Full verification:

```bash
python -m pytest -ra
```

Result:
- `114 passed`
- `1 skipped` (`tests/test_file_edit_e2e.py:327`, POSIX-only permission denial case)

## Risks / Follow-ups

- custom style creation still relies on direct header XML mutation because upstream has no public style-creation API yet
- font family overrides still depend on the current `<hh:fontfaces>` layout
- future Phase 4 reference-preserving analysis tools are tracked in [`docs/follow-up-roadmap.md`](docs/follow-up-roadmap.md)
