---
name: form-fill
description: Use this workflow when filling an existing public-form or administrative HWPX with controlled placeholder replacement, fixed table-cell updates, and structure validation on the current MCP surface.
---

# Form Fill

Use this workflow when a government, school, or public-sector form already exists and the job is to fill approved fields with minimal drift.

## Tool Order

1. Duplicate the approved form with `copy_document`.
2. Read the visible structure with `get_document_outline`, `get_paragraphs_text`, and `get_table_text`.
3. In advanced mode, inspect candidate package parts with `package_parts` and `package_get_text` when placeholders are ambiguous.
4. Replace stable placeholders with `batch_replace`.
5. Use `search_and_replace` for one-off labels or short fixed strings.
6. Use `set_table_cell_text` for fixed form cells.
7. Use `format_text` only for narrow, localized emphasis changes.
8. Run `validate_structure` before handoff.

## Minimize Layout Drift

- Keep table shapes unchanged.
- Prefer editing existing placeholders instead of inserting paragraphs.
- Re-read edited cells or paragraphs after each batch-sized change.

## When To Inspect Package Parts

- The same visible label appears in multiple places.
- The form mixes body text and table content in a way that is hard to target safely.
- You need to confirm which package part actually contains the field content.

## Honest Limitations

- There is no public field-binding API or dedicated template-fill shortcut on the active FastMCP surface.
- Complex seals, drawings, or package-level reference comparison still need future upstream or MCP support.
- Saving is implicit, so fill a copied working file instead of the original form.
