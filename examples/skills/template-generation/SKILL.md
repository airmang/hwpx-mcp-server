---
name: template-generation
description: Use this workflow when generating an HWPX from an approved template or from a new blank document by orchestrating the current MCP text, table, style, and copy tools without adding new public server tools.
---

# Template Generation

Use this workflow when the output should follow a reusable template or a controlled document structure.

## Tool Order

1. If a template file already exists, start with `copy_document`.
2. Replace placeholders with `batch_replace` or `search_and_replace`.
3. Update structured tables with `set_table_cell_text`, `merge_table_cells`, `split_table_cell`, and `format_table`.
4. Add controlled narrative content with `add_heading`, `add_paragraph`, and `insert_paragraph`.
5. Use `create_custom_style` only when repeated new content needs a reusable named style.
6. In advanced mode, run `validate_structure` before handoff.

## Blank-Document Fallback

When no template exists, use:

1. `create_document`
2. `add_heading`
3. `add_paragraph` / `insert_paragraph`
4. `add_table`
5. `format_text` / `format_table`

## Minimize Layout Drift

- Prefer approved templates over rebuilding layout from scratch.
- Use named styles consistently for repeated inserted content.
- Keep placeholder replacement bounded and predictable.

## When To Inspect Package Parts

- A template relies on hidden or non-obvious reference content.
- You need to confirm which section XML contains a repeated placeholder.
- The generated result must match a known package structure closely enough to justify advanced inspection.

## Honest Limitations

- There is no dedicated template-fill shortcut on the active FastMCP surface.
- There is no explicit delayed-save step; edits persist immediately.
- Full reference-structure comparison still belongs upstream or in future MCP work, not in this example skill.
