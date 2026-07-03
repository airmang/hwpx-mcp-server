# Changelog

## [Unreleased]

## [2.15.0] - 2026-07-03
### Added
- **Font shrink-to-fit (M10 follow-on)**: `apply_table_ops` `fill_cell` op now accepts `max_lines` — the cell font is shrunk (down to a floor) so its text fits within that many lines, backed by `hwpx.table_patch` font materialisation (python-hwpx ≥ 2.23.0). Complements `autofit_columns` (width) for the "long text" case.
### Changed
- `python-hwpx>=2.23.0`.
- README trimmed 599→184 lines (the exhaustive tool catalog moved to themed highlights + links to `docs/use-cases.md` / `docs/skill-first-workflows.md`).

## [2.14.0] - 2026-07-03
### Added
- **Column-width fit (M10 follow-on)**: `apply_table_ops` gains two ops — `set_column_widths` (explicit logical column widths, merge-aware) and `autofit_columns` (rebalance widths to content: widen content-heavy columns, narrow light ones, table total preserved) so long text is not cramped in a narrow column. Both are byte-preserving (cellSz only). Backed by `hwpx.table_patch` (python-hwpx ≥ 2.22.0).
### Changed
- `python-hwpx>=2.22.0` (column-width fit).

## [2.13.0] - 2026-07-03
### Added
- **Byte-preserving structural form-fill (M10/S-064)**: `apply_table_ops` — fill cells + edit table structure (`fill_cell`, `delete_column`, `delete_row`, `delete_table`, `insert_row_by_clone`) in one transactional tool that PRESERVES the original table formatting and every untouched byte (never rebuild — the 2026-07-03 failure mode). `delete_column` redistributes freed width and cascades a delete of any row it empties; `insert_row_by_clone` clones a `rowSpan==1` reference row (formatting kept); every structure edit is grid-validated and refuses on an invalid result (fail-closed). `renderCheck='required'|'auto'` gates on / attaches a real-Hancom render verdict. `verify_form_fill` — render before/after in real Hancom → `renderChecked` + overflow/overlap(글자겹침)/pageCount, honest degrade, `require=true` fail-closed. Backed by `hwpx.table_patch` (python-hwpx ≥ 2.21.0); tools return `TABLE_OPS_UNAVAILABLE` on version skew.
### Changed
- `python-hwpx>=2.21.0` (M10 `hwpx.table_patch`).
- **네이티브 자동 차례·상호참조 (M7/S-062)**: `add_toc` — 개요 스타일 제목들로 한컴 네이티브 `TABLEOFCONTENTS` 필드 삽입(`dirty=1` 기본 = 한컴이 처음 여는 순간 항목·스타일·쪽번호 재계산; 방출 쪽번호는 추정치로 정직 표기). `add_cross_reference` — 제목 텍스트로 타깃을 지정하는 쪽 번호 `CROSSREF`(한컴이 자동 재계산). `verify_toc` — 캐시 쪽번호 검증: 구조 verdict + **오라클-free stale 신호**(상호참조↔차례 캐시 모순), `verify_render=True`면 실제 한컴 렌더 대조(`toc_correctness_ratio`), `refresh=True`면 macOS 새로고침 세션 구동, 오라클 없으면 정직 `unverified`, 비-HWPX fail-closed.
### Changed
- python-hwpx 의존 핀 `>=2.19.0` → `>=2.20.0` (`hwpx.tools.toc_author`/`toc_fidelity` + Mac 오라클 refresh 레그).

## [2.11.0] - 2026-07-02
### Added
- **런서식 충실 읽기 표면 (M6/S-060)**: `hwpx_extract_json` 이 `doc.notes[]`(각주/미주 kind·instId·anchorParaIndex·bodyText·bodySpans, PII 마스킹) 를 방출하고, `format_detail=True` 런 상세에 명명 필드 `fontSize`·`fontName`·`superscript`·`subscript` 추가. `hwpx_to_markdown` 은 각주/미주 정의 부록(`[^fn1]: 본문`) 을 덧붙인다 — 이전엔 모든 읽기 표면이 각주 본문을 드롭했다. 정본 `hwpx.tools.read_fidelity` 재사용으로 표면=하니스 일치.
### Fixed
- **strikeout 상시-true 버그**: `_run_format_detail` 이 항상 존재하는 `<hh:strikeout shape="NONE"/>` 의 멤버십만 검사해 모든 런에 취소선을 보고하던 문제 — shape 속성으로 정규화. `underline` type `NONE`→`null` 정규화.
- 기본 테스트 스위트가 라이브 한컴 렌더를 간헐 유발하던 flake(`test_add_tracked_edit_writes_structural_redline_receipt`) — 해당 테스트를 no-oracle degrade 경로로 고정(라이브 렌더는 `HWPX_MAC_ORACLE_SMOKE` opt-in).
### Changed
- python-hwpx 의존 핀 `>=2.18.0` → `>=2.19.0` (read-fidelity 하니스).

## [2.10.0] - 2026-07-01
### Added
- **개인정보(PII) 마스킹 표면 (M5/S-059)**: `scan_personal_info(filename|text)` — read-only PII 감사(유형별 건수 + 마스킹 예시만, 원본값 미노출). `get_document_text`·`hwpx_to_markdown`·`hwpx_extract_json` 에 `mask` 파라미터(기본 ON) — 추출 텍스트의 기계검증 PII(주민등록번호·휴대폰·이메일·카드) 자동 마스킹. `apply_form_fill` 은 채워지는 값 + `applied[]` echo 를 마스킹. `mail_merge` 는 엔진 기본-on 마스킹을 상속. 기계세트=항상-on high-confidence, 맥락형(계좌·주소·이름)=라벨게이트 low-confidence(과마스킹 방지).
### Changed
- python-hwpx 의존 핀 `>=2.17.0` → `>=2.18.0` (PII 마스킹 엔진 `hwpx.tools.pii`).

## [2.9.0] - 2026-06-30
### Added
- `add_tracked_edit(source_filename, destination_filename, edits, author="AI Agent", date=None, dry_run=False)` — redline 저작 MCP 표면 (M4/S-058). `edits[]` 의 `insert`/`delete`/`replace` 를 python-hwpx `add_tracked_*` 프리미티브로 `paragraph_index` 에 적용하고, `verify_redline` 영수증(changeCount/marksLinked/displayEnabled/opensClean/render_checked, 오라클 없으면 정직 강등)을 응답에 fold합니다. in-place·비-.hwpx 거부(fail-closed), `dry_run` 지원. 사람은 한컴 검토 리본에서 수락/거부합니다.
### Changed
- python-hwpx 의존 핀 `>=2.16.0` → `>=2.17.0` (redline 저작 API + 메모 본문 픽스).

## [2.8.0] - 2026-06-29
### Added
- `create_document_from_plan` — M3 document authoring (S-057). When `document_plan.metadata.document_type` is 공문/보고서/가정통신문 the document is composed from a real Hancom-harvested profile (opens-clean), not the from-scratch builder. 공문 supports a 결문 block `document_plan.gyeolmun = {issuer, productionNumber, enforcementDate, disclosure}`. The response `quality` now carries: `gongmun_structure` (공문서 작성규정 구조 hard-gate — 수신·발신명의·시행·공개구분·끝., anchored by a real 시행문; `structure_pass`), `korean_proofing_status` (honest `unverified` / `llm_proofed_not_oracle_verified`, never a silent pass), and `render_checked`/`visual_complete`.
- `create_document_from_plan` `verify_render` param — opt into a real Mac Hancom render receipt (`render_checked`/`visual_complete=true`); absent an oracle it degrades to `unverified` (Constitution V).
### Changed
- `create_document_from_plan` output is **HWPX-only** — a non-`.hwpx` filename (ODT 기안문, docx, pdf) returns `created=false`, `handoff_status="unsupported_format"` with no silent attempt (FR-011; ODT 기안문 is a separate track).
- Require `python-hwpx >= 2.16.0` (M3 document_type routing, 결문 IR, 공문 structure hard-gate, render_checked). Co-located editable resolution for local dev.
### Note
- 각주(footnote) authoring is honest-deferred (`unverified`): `add_footnote` emits valid round-tripping XML but the footnote does not render in Hancom, so it is **not** exposed as a working tool until a real-footnote XML diff fix lands.

## [2.7.0] - 2026-06-26
### Added
- `compose_exam` — 시험지 조판(re-typeset) leap tool (S-056 Plan 3). Pours authored exam Markdown into a school form `.hwpx` using the form's existing named styles, attaches keep-together so no 문항 splits across a column/page, preserves 관리박스 + 머리글/꼬리글 losslessly, and leaves `[그림N]`/`[표N]`/`[식N]` as text placeholders (a human inserts images later). `exam_md` (inline) XOR `exam_md_filename` (path). `verify=True` renders via the Hancom oracle and degrades to `renderChecked=false` when absent; `verify=False` composes without a render. Forms that Hancom exports as vector curves report `splits=null` + `needsReview=true` (no silent 0). Malformed md / unprofilable form → `ok=false`, nothing written (fail-loud). Attaches `openSafety` for the output.
- `verify_question_splits` — standalone honest 문항-split gate (spec 3b): renders via the oracle and runs `measure_question_splits`. No oracle → `renderChecked=false`; curve-export form (0 composed 문항 in the extractable text) → `splits=null` + `needsReview`. `valid_question_numbers` scopes grouping so form chrome (e.g. a "2026." year) can't open a spurious block.
- `set_paragraph_format` keep-together params `keep_with_next` / `keep_lines` / `page_break_before` (spec 3a) — forwarded to the python-hwpx engine's `<hh:breakSetting>` via a freshly minted paraPr (lossless).
### Changed
- Tool surface 88 → 90 (`compose_exam`, `verify_question_splits`); `mcp_server_health` expected count updated and `compose_exam` registered as a key tool.
- Requires `python-hwpx >= 2.15.0` (the `hwpx.exam` 시험지 조판 composer). Imported under a guarded fallback, so an older python-hwpx without `hwpx.exam` leaves the server importable and the exam tools degrade to `ok=false` ("module unavailable").

## [2.6.0] - 2026-06-25
### Added
- `place_seal` / `check_seal_compliance` — oracle-bound 직인/관인 tools (M2 P3 / FR-003). `place_seal` renders the form via the Hancom oracle to locate the 발신명의 anchor, stamps a floating seal on it (`textWrap=IN_FRONT_OF_TEXT` — no text reflow), saves through the openSafety gate, and (verify=True) re-renders to attach the compliance verdict. Falls back to an explicit `anchor_x`/`anchor_y`; with no oracle and no anchor it degrades to `renderChecked=false` rather than guessing. `check_seal_compliance` is the standalone pass/fail check (centered seal passes, mis-placed fails).
- `mail_merge` `fit_mode` (keep/wrap/shrink/wrap_then_shrink/…) + `max_lines` — fit-aware batch (M2 P4 / FR-004): measures each placeholder slot once, isolates slot-overflow / missing-field rows into `needsReview[]` / `skipped[]` (`fitAware` in the report). Excel/CSV/XLSX 명부 reachable via python-hwpx ingestion.
- `[oracle]` extra (`python-hwpx[visual]` → PyMuPDF) for the seal/form-fill render-oracle path; absent it degrades honestly (`renderChecked=false`), never crashes.
### Changed
- Require `python-hwpx >= 2.14.0` (seal placement, `extract_image_boxes`, `mail_merge` fit_policy + xlsx, `isEmbeded` image-render fix).
- Tool surface 86 → 88 (`place_seal`, `check_seal_compliance`); `mcp_server_health` expected count updated.

## [2.5.0] - 2026-06-24
### Added
- VisualComplete quality contract (no model can bypass the gate): every write funnels through python-hwpx's single `SavePipeline` and the capability handshake, and write responses carry a `visualComplete` block (`ok`/`status`/`errorCodes`/`warnings`/`suggestedRetry`). (`byte_preserving_patch` is a byte-preserving fast path: open-safety + capability gated, render gate N/A by design.)
- `quality` block on writes (default `transparent`; `strict` or per-field overrides like `overflowPolicy`/`layoutLint`). On a gate failure the save is withheld (`ok=false`) and the model gets a structured, retry-able error (`FIELD_OVERFLOW`, `STALE_LINESEG_DETECTED`, `VISUAL_COMPLETE_FAILED`, …) with `suggestedRetry`. New `HWPX_MCP_QUALITY` global default.
- Capability handshake in `mcp_server_health` (core/mcp/plugin versions + fingerprint hash) that **fails closed** on skew — writes are blocked when the installed python-hwpx can't honour the gate. Bypass with `HWPX_MCP_REQUIRE_CAPABILITY=0`.
- README "no raw XML" quality-contract section.

### Changed
- Require `python-hwpx >= 2.12.0` (the VisualComplete quality stack: `hwpx.quality` SavePipeline, `form_fit`, `layout`, `design`).

## [2.4.1] - 2026-06-12
### Changed
- Require `python-hwpx >= 2.11.1` so document-plan generated headings receive real `개요 N`/`Outline N` paragraph styles and visible title/heading hierarchy.

### Fixed
- `create_document_from_plan` outputs now round-trip through `get_document_outline` as structured headings instead of plain emphasized paragraphs.
- `get_document_outline` no longer promotes plain short or numbered paragraphs when a document has outline styles; legacy markdown `#` heading fallback remains available for older generated files.

## [2.4.0] - 2026-06-12
### Added
- Transactional editing: `apply_edits` (atomic multi-op with rollback, `dry_run`, `expected_revision`, `idempotency_key`), `undo_last_edit`, automatic `.bak` rotation, and semantic diff summaries on write responses.
- `render_preview` layout preview tool (page-approximate HTML/PNG for agent self-checks).
- Document revision concurrency guard: reads return `document_revision`; writes reject on `expected_revision` mismatch; Hancom file-lock warnings.
- Native form field (누름틀) workflows: `list_form_fields`, `fill_form_field`, plus match-confidence grades in `analyze_form_fill`.
- Existing-document format editing tools: `set_paragraph_format`, `set_page_setup`, header/footer/page-number and list/bullet tools (human units).
- Official document style lint `inspect_official_document_style` and approval-box (결재란) preset support.
- Advanced generator tools: photo sheet (`image_grid`), meeting nameplates, table-based org chart.
- `doc_diff` paragraph diff and reference-consistency lint tools.
- `mail_merge` bulk generation and `table_compute` (sum/avg) tools.
- Style profile transfer (`extract_style_profile`) and template registry tools.
- Picture asset workflows (safe insert/replace with manifest validation).
- Byte-preserving patch tool `byte_preserving_patch` backed by `hwpx.patch`.
- `get_document_map` single-call document map; compact write responses (`verbosity` compact/full); plugin health diagnostics in `mcp_server_health`; actionable `suggestion` fields on common errors.

### Changed
- Require `python-hwpx >= 2.11.0` for the fuzz-hardened, parser-hardened authoring surface backing the new tools.

### Fixed
- `add_heading` no longer stores a literal markdown `#` prefix in document text (it leaked into the Hancom editor view). Headings now use the template's built-in `개요 N` paragraph styles with emphasized run styling; outline readers (`get_document_outline`, structure extraction, form-fill analysis) detect style-based headings first while still recognizing legacy `#` headings, and a paragraph added right after a heading no longer inherits the outline style.

## [2.3.5] - 2026-06-09
### Changed
- Require `python-hwpx >= 2.10.3` so MCP saves inherit the upstream editor-open safety guard for stale `lineSegArray` layout caches.

### Fixed
- Add an open-safety save gate for local and HTTP storage. Saves are written to a temporary target, checked for blocking package validation failures and reopenability, and only then replace or upload the document.
- Add open-safety verification evidence to direct generated-document and repair paths, including document-plan, proposal, quality-generation, form-fill, and `repair_hwpx` outputs.
- Return `verification.openSafety` evidence from stateless `create_document` so blank-document creation has the same handoff signal as other generated outputs.
- Block `copy_document` from creating a new HWPX from an unsafe source and preserve an existing destination when validation fails.
- Return `openSafety` evidence from successful `copy_document` calls so copied HWPX handoffs expose the same editor-open signal as generated outputs.
- Save generated document-plan/proposal and quality-generation outputs to sibling temporary files first, then replace the requested destination only after open-safety verification passes.
- Return `verification` and `openSafety` evidence from HWP-to-HWPX conversion outputs.
- Return `verificationReport.openSafety` evidence from `make_blank` and `fill_template` outputs.
- Return `verificationReport` plus top-level `openSafety` evidence from stateless edit tools such as text replacement, paragraph/table edits, formatting, and memo operations.
- Block unsafe HTTP downloads from being promoted into the local cache; remote payloads are first written to a temporary file and open-safety checked.
- Apply form-fill changes to a sibling temporary HWPX and replace the destination only after structure, package, document, and open-safety validation pass.
- Include `repair.openSafety` in successful `apply_form_fill` responses so the repair/repack step exposes its own editor-open evidence.
- Fail closed when an older `python-hwpx` installation lacks the editor-open safety classifier or repair helper, instead of importing the MCP server with weak save validation.
- Fail closed in quality-generation validation when package validation support is unavailable, so generated HWPX cannot be handed off without package/document/open-safety evidence.
- Preserve the HTTP storage cache when remote upload fails by replacing the cache only after temporary save, open-safety verification, and upload all succeed.
- Inherit upstream repair/recover cleanup for stale `lineSegArray` layout caches so `repair_hwpx` can fix that editor-open failure class instead of only rejecting it.
- Inherit upstream save-time normalization for named paragraph `styleIDRef` values so existing malformed documents can be edited and saved with numeric style references.
- Preserve the previous target when a save fails open-safety verification.
- Surface the stricter upstream `openSafety.ok` signal, including hard document-validation failures in addition to package and reopen failures.

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
