# HWPX MCP Server

`hwpx-mcp-server` is a pure-Python [Model Context Protocol](https://github.com/modelcontextprotocol/specification) server that
exposes rich tooling around the [`python-hwpx`](https://github.com/airmang/python-hwpx) library. The server allows local HWPX
files to be inspected, searched, edited and saved through any MCP-compatible client such as Gemini or Claude.

## Features

- StdIO based MCP server implemented with the official `mcp` Python SDK.
- Secure workdir enforcement via the `HWPX_MCP_WORKDIR` environment variable.
- Text extraction with pagination, search and style-aware replacements.
- Document editing helpers for paragraphs, tables, memos, shapes and OPC parts.
- Optional automatic backup before destructive operations.
- Pure Python implementation designed to run instantly through [`uvx`](https://github.com/astral-sh/uv).

## Quick start

```bash
uvx hwpx-mcp-server
```

When adding the server to an MCP client configuration use the following template:

```json
{
  "mcpServers": {
    "hwpx": {
      "command": "uvx",
      "args": ["hwpx-mcp-server"],
      "env": {
        "HWPX_MCP_WORKDIR": "C:/Docs",
        "HWPX_MCP_PAGING_PARA_LIMIT": "2000",
        "HWPX_MCP_AUTOBACKUP": "1",
        "LOG_LEVEL": "INFO"
      }
    }
  }
}
```

The workdir must exist and the server will refuse to operate outside of the configured root.

## Environment variables

| Variable | Description | Default |
| --- | --- | --- |
| `HWPX_MCP_WORKDIR` | Absolute path that constrains all file operations. **Required.** | – |
| `HWPX_MCP_PAGING_PARA_LIMIT` | Maximum number of paragraphs returned by pagination-aware tools. | `2000` |
| `HWPX_MCP_AUTOBACKUP` | When `1`, creates `<file>.bak` before destructive saves. | `0` |
| `HWPX_MCP_ENABLE_OPC_WRITE` | Set to `1` to allow `package_set_text` / `package_set_xml`. | `0` |
| `LOG_LEVEL` | Logging level (`DEBUG`, `INFO`, …) output as JSON lines to stderr. | `INFO` |

## Available tools

The server registers the following MCP tools:

- **open_info** – return document metadata and paragraph/header counts.
- **list_sections**, **list_headers** – inspect structural sections and header references.
- **read_text**, **text_extract_report** – extract text with pagination and annotation rendering.
- **find**, **find_runs_by_style**, **replace_text_in_runs** – search and replace helpers.
- **add_paragraph**, **insert_paragraphs_bulk**, **add_table**, **set_table_cell_text**, **replace_table_region** – editing helpers.
- **add_shape**, **add_control**, **add_memo**, **attach_memo_field**, **add_memo_with_anchor**, **remove_memo** – object level utilities.
- **ensure_run_style**, **list_styles_and_bullets**, **apply_style_to_paragraphs** – style management.
- **save**, **save_as**, **make_blank** – persistence helpers.
- **package_parts**, **package_get_text**, **package_set_text**, **package_get_xml**, **package_set_xml** – OPC part access (write tools gated by `HWPX_MCP_ENABLE_OPC_WRITE`).
- **object_find_by_tag**, **object_find_by_attr** – XML element discovery.
- **validate_structure**, **lint_text_conventions** – validation and linting utilities.
- **list_master_pages_histories_versions** – manifest level metadata summary.

Each tool is described by a JSON schema surfaced through the `ListTools` response, enabling the client to perform structured
validation before issuing a call.

## Tests

A small pytest suite exercises the filesystem guard and core document operations. Run the tests after installing the package
requirements:

```bash
python -m pip install -e .[test]
python -m pytest
```

## Development notes

- The server is implemented entirely in Python and depends on `python-hwpx`, `mcp`, `anyio`, `pydantic` and `modelcontextprotocol`.
- Tool handlers operate directly on `HwpxDocument` instances and always resolve paths using `WorkdirGuard` to avoid directory
  traversal.
- Dry-run flags are honoured for destructive operations and `.bak` files are created automatically when backups are enabled.

## License

This project is licensed under the MIT License – see the [LICENSE](LICENSE) file for details.
