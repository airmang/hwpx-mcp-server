# Skill-First Workflows on the Current MCP Surface

This guide assumes the current FastMCP server is the stable product surface and `python-hwpx` remains the upstream engine layer.

Scope for this pass:

- do not add new public MCP tools by default
- keep document/package semantics in Python code, not in markdown
- use skills as thin orchestration over the current tool inventory

## Current Tool Coverage

`HWPX_MCP_ADVANCED=1` is required for package inspection, validation, and plan/review tools.

| Workflow need | Existing MCP tools | Notes |
|---|---|---|
| Read package parts | `package_parts` | Advanced mode only. |
| Extract XML/text | `package_get_xml`, `package_get_text`, `get_document_text`, `get_paragraph_text`, `get_paragraphs_text`, `hwpx_extract_json`, `hwpx_to_markdown`, `hwpx_to_html` | Use package-level extraction when exact part shape matters. |
| Validate structure | `validate_structure`, `lint_text_conventions` | `lint_text_conventions` is optional style/text policy linting. |
| Plan edits | `plan_edit` | Review/verification planning only. |
| Preview plans | `preview_edit` | Shows the plan artifact before confirmation. |
| Apply plans | `apply_edit` | Applies the review pipeline state, not general direct HWPX mutations. |
| Template fill | `copy_document` + `batch_replace` / `search_and_replace` / `set_table_cell_text` | No public `fill_template` tool on the active FastMCP surface. |
| Save/copy flows | `copy_document`, `save`, `save_as`, plus the normal mutating tools | Mutating tools persist immediately through the shared atomic save path; `save`/`save_as` additionally return a post-save `verificationReport`. |

Important boundary:

- There is no active public `fill_template` tool on the FastMCP surface.
- Cautious workflows should edit a copied working file, then promote that file externally after review.

## Workflow 1: Reference-Preserving Edit

Use when an existing HWPX document must be edited with minimal layout drift and clear review checkpoints.

Suggested order:

1. `copy_document` to create a working copy.
2. Read the baseline with `get_document_outline`, `get_paragraphs_text`, `get_table_text`, or `hwpx_extract_json`.
3. In advanced mode, inspect likely-sensitive parts with `package_parts`, `package_get_xml`, `package_get_text`, `object_find_by_tag`, and `object_find_by_attr`.
4. Run `validate_structure` before editing if the source package is suspicious or externally produced.
5. Make bounded edits with the smallest tool that fits the task:
   - `search_and_replace` or `batch_replace` for anchored text updates
   - `format_text` for run-level styling
   - `set_table_cell_text` for fixed table locations
   - `insert_paragraph`, `add_paragraph`, `add_heading`, `add_memo` only when structure really must change
6. Re-read the touched areas and rerun `validate_structure`.
7. If a review artifact is useful, use `plan_edit` and `preview_edit` to capture the intended change flow before or alongside manual review.

Layout drift controls:

- Prefer replacements over structural insert/delete operations.
- Touch the smallest paragraph/table range possible.
- Inspect package parts when the source relies on fixed section XML, memo anchors, or table-heavy layouts.
- Use `copy_document` first because edits persist immediately.

Limitations:

- `plan_edit`, `preview_edit`, and `apply_edit` are not a full semantic patch engine for arbitrary HWPX edits.
- There is no public structure-diff or layout-drift report tool on the active surface.
- Package inspection is diagnostic; interpretation still belongs to the workflow layer.

## Workflow 2: Government or Public-Form Filling

Use when a public-sector or administrative form already exists and the main task is controlled field replacement with low drift.

Suggested order:

1. `copy_document` from the official form or approved reference copy.
2. Read the visible structure with `get_document_outline`, `get_paragraphs_text`, and `get_table_text`.
3. If placeholders are unclear, inspect relevant parts with `package_parts` and `package_get_text`.
4. Fill stable placeholders with `batch_replace`.
5. Fill one-off labels or small fixed strings with `search_and_replace`.
6. Fill table cells with `set_table_cell_text`.
7. Use `format_text` only for clearly localized emphasis changes.
8. Run `validate_structure` before handing off the filled form.

Layout drift controls:

- Prefer placeholder replacement over inserting new paragraphs.
- Keep table geometry unchanged unless the form explicitly allows it.
- Re-read the affected paragraphs or cells after each batch-sized edit.

Limitations:

- There is no public field-binding API for government forms.
- Repeated labels can still require human disambiguation.
- Complex approval seals, drawing objects, or package-level reference comparisons still need upstream or future MCP support.

## Workflow 3: Template-Based Document Generation

Use when document generation starts from a reusable template, or when a new document must be assembled from current core mutators.

Suggested order:

1. If a template file exists, start with `copy_document`.
2. Replace placeholders with `batch_replace` or `search_and_replace`.
3. Update fixed tables with `set_table_cell_text`, `merge_table_cells`, `split_table_cell`, or `format_table`.
4. Add controlled freeform content with `add_heading`, `add_paragraph`, `insert_paragraph`, and `add_table`.
5. Use `create_custom_style` only when a real reusable style is needed across multiple inserts.
6. Run `validate_structure` if advanced mode is available.

Fallback when no template exists:

1. `create_document`
2. `add_heading`
3. `add_paragraph` / `insert_paragraph`
4. `add_table`
5. `format_text` / `format_table`

Limitations:

- There is no single public `fill_template` shortcut on the active surface.
- Large-scale document assembly still requires explicit orchestration in the client or skill.

## Workflow 4: Cautious Edit-Review-Save

Use when edits should remain reviewable and low risk even though the current MCP surface persists mutations immediately.

Suggested order:

1. `copy_document` to create a disposable working draft.
2. Capture the baseline with `get_document_text`, `get_document_outline`, or advanced package reads.
3. Use `plan_edit` and `preview_edit` when you want an explicit review/verification artifact.
4. Perform only bounded mutating operations on the working draft.
5. Re-read the touched areas and rerun `validate_structure`.
6. Keep or rename the reviewed copy outside the MCP workflow once approved.

Why this is the safe path:

- The MCP server exposes `save` and `save_as`, both returning a post-save `verificationReport` for explicit handoff checks.
- Mutations already go through the safer atomic save path, but they still update the target file immediately.
- Copy-first is therefore the practical review boundary on the current surface.

## Example Skill Folders

- `examples/skills/reference-preserving-edit/SKILL.md`
- `examples/skills/form-fill/SKILL.md`
- `examples/skills/template-generation/SKILL.md`

These examples stay intentionally thin:

- they describe when to use the workflow
- they name the existing MCP tools in order
- they document layout-drift precautions
- they do not duplicate Python business logic from the server or from `python-hwpx`

## Prompt/Resource Layer Status

No new prompt or resource layer was added in this pass.

Reason:

- the active product surface is the FastMCP tool inventory in `src/hwpx_mcp_server/server.py`
- legacy prompt/resource code exists elsewhere in the repo, but enabling or expanding it here would broaden scope without proving a product need

If future workflow prompting becomes important, keep the contract narrow and register it against the active FastMCP surface instead of reviving legacy behavior by default.
