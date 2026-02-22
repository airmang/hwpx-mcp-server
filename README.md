# hwpx-mcp-server

Stateless MCP server for reading and editing HWPX documents with `python-hwpx`.

## What Changed in v2

- Stateless tool design: every tool receives `filename` (or `source_filename`) and works in one call.
- No session/handle IDs in default workflow.
- Token-safe text responses with `max_chars` limits.
- LLM-friendly tool names (`get_document_text`, `find_text`, `search_and_replace`, `batch_replace`).
- Advanced XML/pipeline tools are opt-in with `HWPX_MCP_ADVANCED=1`.

## Install

```bash
uvx hwpx-mcp-server
```

Requirements:

- Python 3.10+
- `python-hwpx >= 1.9`

## MCP Client Config

Example (`claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "hwpx": {
      "command": "uvx",
      "args": ["hwpx-mcp-server"]
    }
  }
}
```

## Core Tool Surface

### Document

- `create_document(filename, title?, author?)`
- `get_document_info(filename)`
- `get_document_text(filename, max_chars?)`
- `get_document_outline(filename)`
- `list_available_documents(directory?)`
- `copy_document(source_filename, destination_filename?)`

### Content

- `add_heading(filename, text, level?)`
- `add_paragraph(filename, text, style?)`
- `insert_paragraph(filename, paragraph_index, text, style?)`
- `delete_paragraph(filename, paragraph_index)`
- `add_table(filename, rows, cols, data?)`
- `get_table_text(filename, table_index?)`
- `set_table_cell_text(filename, table_index, row, col, text)`
- `add_page_break(filename)`
- `add_memo(filename, paragraph_index, text)`
- `remove_memo(filename, paragraph_index)`

### Search / Replace

- `find_text(filename, text_to_find, match_case?, max_results?)`
- `search_and_replace(filename, find_text, replace_text)`
- `batch_replace(filename, replacements)`

`batch_replace` runs in order and saves once after all replacements.

### Formatting

- `format_text(filename, paragraph_index, start_pos, end_pos, ...)`
- `create_custom_style(filename, style_name, ...)`
- `list_styles(filename)`
- `format_table(filename, table_index, has_header_row?)`
- `merge_table_cells(filename, table_index, start_row, start_col, end_row, end_col)`
- `split_table_cell(filename, table_index, row, col)`

## Advanced Mode (Opt-in)

Set `HWPX_MCP_ADVANCED=1` to enable advanced/debug tools:

- `package_parts`
- `package_get_xml`
- `package_get_text`
- `object_find_by_tag`
- `object_find_by_attr`
- `plan_edit`
- `preview_edit`
- `apply_edit`
- `validate_structure`
- `lint_text_conventions`

These tools are hidden by default.

## Environment Variables

| Variable | Description | Default |
|---|---|---|
| `HWPX_MCP_MAX_CHARS` | Default max text size for text-returning tools | `10000` |
| `HWPX_MCP_AUTOBACKUP` | Create `.bak` before save when `1` | `1` |
| `LOG_LEVEL` | Log level | `INFO` |
| `HWPX_MCP_ADVANCED` | Enable advanced tools when `1` | `0` |

## Example: Year Rollover

```python
batch_replace("school_plan.hwpx", [
  {"find": "2026", "replace": "2027"},
  {"find": "2025", "replace": "2026"},
])
```

## Development

Run tests:

```bash
pytest -q
```

Run server (stdio transport):

```bash
hwpx-mcp-server
```
