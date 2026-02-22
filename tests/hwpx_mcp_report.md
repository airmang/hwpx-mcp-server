# HWPX MCP Use Case Test Report

Date: 2026-02-22
Sample: `hwpx_mcp_test.hwpx` (copy of `2026학년도 교육정보부 운영계획.hwpx`)

## Scope
- Read operations: document info, outline, text, paragraph range, find text
- Edit operations: insert/add paragraph, add heading, page break
- Formatting: text formatting, custom style creation, style application
- Tables: add table, set/get cell text, merge/split cells, table formatting
- Memo: add/remove
- Copy/save: document copy

## Results Summary

### Read & Search
- `hwpx_get_document_info` worked; sizes/paragraph counts updated after edits.
- `hwpx_get_document_outline` and `hwpx_get_document_text` worked (text truncated as expected when max_chars provided).
- `hwpx_get_paragraph_text` / `hwpx_get_paragraphs_text` worked.
- `hwpx_find_text` worked for multiple matches (e.g., "테스트").

### Paragraph & Heading Edits
- `hwpx_insert_paragraph` succeeded when `style` is an empty string; fails if `style` is omitted or `null`.
- `hwpx_add_paragraph` succeeded when `style` is an empty string; fails if `style` is omitted or `null`.
- `hwpx_add_heading` succeeded; outline shows a "## " prefix in the heading text.
- `hwpx_add_page_break` succeeded.

### Text Formatting
- `hwpx_format_text` succeeded with start/end positions and color hex (e.g., "FF0000").
- `hwpx_create_custom_style` and `hwpx_list_styles` worked; style applied via `hwpx_add_paragraph`.

### Table Operations
- `hwpx_add_table` succeeded and returned a table index.
- `hwpx_get_table_text` returned expected table content.
- `hwpx_set_table_cell_text` worked.
- `hwpx_merge_table_cells` and `hwpx_split_table_cell` worked; after split, a merged header cell resulted in an empty adjacent cell as expected.
- `hwpx_format_table` worked with `has_header_row=true`.

### Memo
- `hwpx_add_memo` failed with lxml/ElementTree mismatch error.
- `hwpx_remove_memo` returned success (even after add failed).

### Copy/Save
- `hwpx_copy_document` worked (created `hwpx_mcp_test_copy.hwpx`).

## Issues / Gaps Found

1. `hwpx_insert_paragraph` / `hwpx_add_paragraph`
   - Error when `style` is omitted or `null`:
     - "Input should be a valid string" (Pydantic validation error)
   - Suggestion: allow optional `style` with default or accept `null`.

2. `hwpx_search_and_replace`
   - Could not replace the displayed title text even with exact string from `hwpx_get_paragraph_text`.
   - `replaced_count` remained 0 for multiple variants.
   - Suggestion: clarify whether it expects raw XML, normalized whitespace, or exact run-level matching.

3. `hwpx_add_memo`
   - Error: "SubElement() argument 1 must be xml.etree.ElementTree.Element, not lxml.etree._Element".
   - Suggestion: align XML element types (lxml vs ElementTree) or normalize internally.

4. `hwpx_batch_replace`
   - Tool signature mismatch: runtime expects `replacements` but schema only shows `filename`.
   - Suggestion: update MCP schema or accept optional default.

5. Paragraph deletion behavior
   - After deleting inserted paragraph, two blank paragraphs remained near the top (indices 1 and 2).
   - Suggestion: ensure delete removes the intended paragraph cleanly without leaving empty placeholders.

6. Table count in `hwpx_get_document_info`
   - Table count remained `8` after `hwpx_add_table` (which returned a new table index).
   - Suggestion: verify table count updates or whether the count is based on original tables only.

## Artifacts
- Modified test doc: `hwpx_mcp_test.hwpx`
- Copy: `hwpx_mcp_test_copy.hwpx`

## Recommended Follow-ups
- Fix `hwpx_add_memo` element type mismatch.
- Update tool schemas for `hwpx_batch_replace` and optional `style` fields.
- Clarify/adjust `hwpx_search_and_replace` matching rules (whitespace/run handling).
- Verify table count and paragraph deletion behavior.
