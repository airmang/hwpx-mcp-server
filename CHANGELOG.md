# Changelog

## [Unreleased]
### Changed
- License relicensed to Apache-2.0 (sole author, full consent).
- Previous license terms no longer apply to future releases.
- Sync downstream docs to the latest local stack validation baseline: `python-hwpx 2.9.0` verified on 2026-04-15, documented minimum support remains `python-hwpx >= 2.6`.

### Added
- Add stack smoke-test workflow and benchmark follow-up docs under `python-hwpx/shared/hwpx` so the shared HWPX stack baseline lives with the upstream engine repo.

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
