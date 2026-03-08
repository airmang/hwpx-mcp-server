# Phase 3: Upstream-Aware Hardening

## Summary

This PR handles Phase 3 only.

- audits the remaining `python-hwpx` integration points in `server.py`, `hwpx_ops.py`, and `core/`
- introduces `src/hwpx_mcp_server/upstream.py` as a thin downstream adapter for non-obvious upstream calls
- removes duplicated upstream-facing logic from `core/formatting.py`, `hwpx_ops.py`, `core/document.py`, `storage.py`, and `server.py`
- adds regression coverage for the refactored adapter path

No new MCP tools are added in this PR.

## Why

`hwpx-mcp-server` is the downstream product layer. Before this change, several fragile `python-hwpx` touchpoints were duplicated across the repo:

- direct document/package/extractor/export imports in multiple files
- duplicated char-style creation logic in `core/formatting.py` and `hwpx_ops.py`
- direct lookups of private upstream internals like `_DEFAULT_CELL_WIDTH`

That made upstream upgrades harder to review and easier to break in more than one place at a time.

## What Changed

### New adapter boundary

- added `src/hwpx_mcp_server/upstream.py`
- centralized:
  - document open/new/template creation
  - package open
  - text extractor creation
  - object finder creation
  - validation entrypoint
  - exporter entrypoints
  - private `_DEFAULT_CELL_WIDTH` lookup
  - shared style/header helper logic

### Refactors onto the adapter

- `server.py` now uses the adapter for raw payload parsing
- `core/document.py` and `storage.py` now use the adapter for document open/template bootstrap
- `core/formatting.py` now delegates shared style/char-property logic to the adapter
- `hwpx_ops.py` now uses the adapter for text extraction, package access, object finding, validation, exporters, and shared char-style creation

### Tests

- updated `tests/test_hwpx_ops.py` to patch the adapter entrypoint (`create_text_extractor`) instead of the raw upstream class
- added a regression test proving `hwpx_ops` uses the shared char-style integration path for colored run styles

## Tests Run

Focused integration pass in a clean virtualenv against released `python-hwpx 2.7.1`:

```bash
python -m pytest -q tests/test_formatting.py tests/test_http_storage.py tests/test_hwpx_ops.py
```

Full verification:

```bash
python -m pytest -ra
```

Result:
- focused pass: `46 passed`
- full pass: `114 passed`, `1 skipped`

## Remaining Version-Sensitive Areas

- style creation still depends on private upstream header access via `_styles_element()`
- shared char-style creation still depends on header-level `ensure_char_property()`
- border-fill creation still uses private header helpers for ID allocation/update
- memo fallback still writes raw XML directly when mixed ET/lxml behavior breaks the native path
- local developer shells can still accidentally import a dirty sibling `python-hwpx` checkout unless validation is isolated
