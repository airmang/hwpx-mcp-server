# Changelog

## [Unreleased]

## [2.3.4] - 2026-06-06
### Added
- Add a shared paragraph location contract covering body paragraphs and table-cell paragraphs, plus anchors that can be passed between search, lookup, memo, and edit tools.
- Add `get_location_text`, `add_memo_by_anchor`, `replace_in_paragraph`, `replace_by_anchor`, and `mcp_server_health`.

### Changed
- Search now returns reusable `location` and `anchor` values for body and table-cell matches.
- `set_table_cell_text` supports `preserve_format` and `split_paragraphs`, preserving existing run `charPrIDRef` while replacing text.
- `get_table_map` separates `caption_text` from `preceding_paragraph_text` and keeps cell paragraph boundaries in previews.
- Require `python-hwpx >= 2.10.2` for the table location and table-cell formatting behavior.

### Fixed
- Clarify sandbox path errors so users know to use a relative path under the sandbox root or an absolute path inside that root.

## [2.3.3] - 2026-06-04
### Added
- Expose document-plan validation, analysis, creation, authoring-quality, operating-plan quality, template form-fit, proposal quality, and repair workflows through MCP.
- Add `create_government_report_document`, `compute_report_value`, and `parse_government_report_text` MCP tools backed by `python-hwpx` government-report/report utility APIs.

### Changed
- Require `python-hwpx >= 2.10.1` so installed MCP servers have document-plan v2, government-report preset, report calculators/parser, table cleanup, and id-integrity support.

## [2.3.2] - 2026-06-04
### Fixed
- Clear stale `lineSegArray` layout caches when placeholder form-fill inserts text into an existing paragraph.
- Clear layout caches when the single remaining paragraph is emptied by `delete_paragraph`, so Hancom recalculates rendered text instead of reusing stale line layout.

## [2.3.1] - 2026-06-04
### Fixed
- Prevent Hancom glyph overlap after replacing text in existing HWPX paragraphs by collapsing cross-run replacements into the first run instead of redistributing text across stale run boundaries.
- Clear stale `lineSegArray` layout caches in XML fallback table-cell replacement paths so Hancom recalculates line layout after edits.

## [2.3.0] - 2026-06-02
### Added
- Add stack smoke-test workflow and benchmark follow-up docs under `python-hwpx/shared/hwpx` so the shared HWPX stack baseline lives with the upstream engine repo.

### Changed
- Require `python-hwpx >= 2.10.0` so `uvx hwpx-mcp-server` and plugin fallback launchers resolve the S-013 builder core, authoring-quality, validation-severity, and template/form-fill surface shipped by the upstream engine.
- Refresh README requirements to the `python-hwpx 2.10.0` public stack baseline.

## [2.2.6] - 2026-04-27
### Changed
- Require `python-hwpx >= 2.9.1` so downstream consumers pick up the upstream interop fixes for `ET.SubElement` on lxml elements (airmang/python-hwpx#30) and the signed int32 ID generators (airmang/python-hwpx#34, #35).
- License relicensed to Apache-2.0 (sole author, full consent); previous license terms no longer apply to future releases.

### Removed
- Drop the `_patch_upstream_id_generators_to_signed_int32` compat shim and its regression tests. The shim existed only to bridge users still pinned to `python-hwpx 2.9.0`; it is superseded by the upstream fix in `python-hwpx 2.9.1`. The `_patch_sub_element_for_lxml_parent` shim is retained because `hwpx/oxml/document.py` still carries stdlib `ET.SubElement` call sites outside the cell-text and run-style paths that 2.9.1 fixed. Thanks to [@seonghoony](https://github.com/seonghoony) for the original shim in #64.

### Fixed
- Drop the legacy `License :: OSI Approved :: Apache Software License` classifier that coexisted with the PEP 639 `license` expression in `pyproject.toml`, which broke `pip install -e .` and `python -m build` under `setuptools>=77`.

## [2.2.5]
- Add filename-based MCP tools `get_table_map`, `find_cell_by_label`, and `fill_by_path` on top of the upstream `python-hwpx` table navigation helpers.
- Keep the downstream layer thin by limiting this integration to validation, document open/save handling, and LLM-friendly structured JSON responses.
- Add regression coverage for table discovery shape, Korean label normalization, ambiguous/out-of-bounds path reporting, persisted fills, and filename-only MCP schemas.
- Refresh README and workflow docs for the new table/form helpers and remove stale claims about public `save` / `save_as` tools.

## [2.2.4]
- README를 기존 레이아웃 스타일에 맞춰 정리하고 문서를 한글 중심으로 재정비했습니다.
- 패키지 소개와 설치, MCP 설정, 주요 도구, 환경 변수 중심으로 문서 구조를 다듬었습니다.
- HTTP 전송 관련 설명과 과도한 내부 구현 설명을 제거해 PyPI 설명을 간결하게 정리했습니다.

## [2.2.3]
- Clarify the post-pivot product boundary so release-facing docs consistently treat `python-hwpx` as the upstream engine, `hwpx-mcp-server` as the active FastMCP product surface, and skills/workflows as orchestration only.
- Add skill-first workflow guidance and thin example skills for reference-preserving edit, public-form filling, template-based generation, and cautious copy-first review flows without adding new public MCP tools.
- Add release-readiness documentation and final scope-alignment notes, and explicitly defer non-surface items such as public `fill_template`, public `save_as`, structure diff, and layout-drift reporting.

## [2.2.2]
- Isolate `python-hwpx` integration behind a dedicated downstream adapter and reduce duplicated upstream-facing logic across the MCP server, core helpers, and `HwpxOps`.
- Fix advanced MCP wrappers so `object_find_by_attr` works with attribute-only queries and `plan_edit` / `preview_edit` / `apply_edit` reflect the currently implemented hardened verification flow instead of sending invalid payloads.
- Remove memo anchor remnants when `remove_memo` runs so memo IDs no longer leak into paragraph text after deletion.
- Add real-output regression coverage for advanced tool wrappers, memo cleanup, and memo-polluted paragraph planning behavior.

## [2.2.1]
- Require `python-hwpx >= 2.6` for the documented MCP feature set and verify downstream compatibility against released `python-hwpx 2.7.1` in a clean environment.
- Make `format_text` persist real run-level `charPrIDRef` changes instead of returning success after a no-op style rewrite.
- Make `create_custom_style` return a reusable `style_id` backed by a distinct upstream `charPr` when formatting overrides are requested, and resolve style names to real style IDs in `add_paragraph` / `insert_paragraph`.
- Route local write paths through the shared atomic save flow (`temp -> validate -> replace`) instead of mixing direct `save_to_path()` writes with storage-backed writes.

## [2.2.0]
- Stabilize tests when an inherited `HWPX_MCP_SANDBOX_ROOT` would otherwise block pytest temp paths.
