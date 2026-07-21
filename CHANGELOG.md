# Changelog

## [5.0.0] - 2026-07-21

### Removed (breaking, 5.0.0 major boundary)
- The five one-transition stubs `plan_edit`, `preview_edit`, `apply_edit`,
  `analyze_quality_generation`, and `apply_quality_generation` are removed from
  the tool registry, the explicit bindings, the FastMCP server aliases, and the
  workflow `TRANSACTIONAL_EDIT` allowlist. No alias and no ghost wrapper stands
  in for them (S-078 policy). Migrate per `docs/deprecation-5.0.0.md`:
  `apply_document_commands` (dry-run for the old plan/preview semantics) for the
  edit trio, and `create_document_from_plan` + `inspect_document_quality` for
  quality generation.

### Changed (breaking, 5.0.0 major boundary)
- The three template-formfit facades `analyze_template_formfit`,
  `apply_template_formfit`, and `fill_form_field` demote from
  `COMPATIBILITY` to `DEPRECATED`. Their handlers and behaviour are unchanged;
  they now carry the one-transition deprecation guidance toward the canonical
  `analyze_form_fill`/`apply_form_fill`/`verify_form_fill` trio and are slated
  for removal at the next major boundary.
- Baseline classification drops 136 → 131 (compatibility 9 → 6, deprecated
  5 → 3); the installed advanced surface is 132 → 127, the default surface
  121 → 119, and skill-required stays 28. The contract hash moves
  `c89cbc5f98eb5367` → `c9a451a7c003752a`; the delta is recorded in
  `docs/tool-contract-delta-5.0.0.json` and cross-checked against the live
  registry by `scripts/render_contract_delta.py`.

### Internal
- `handlers/form_fill.py` now imports the template-formfit callables from the
  `hwpx.template_formfit` submodule instead of the `hwpx` top level. At the core
  4.0.0 boundary the top-level re-exports become deprecated shims that warn on
  access; the demoted-but-functional MCP tools must not emit a runtime warning.
  The submodule path is stable across core 3.3.1 and 4.0.0.

## [4.4.1] - 2026-07-21

### Notes
- `v4.4.0`은 릴리스 게이트 실패로 미발행 보존 태그입니다(prepublish가 core를
  `[preview]` extra 없이 설치해 MathML 성공경로 테스트가 정직 실패 — 워크플로
  설치 라인 수정으로 복구). PyPI 산출물은 존재하지 않습니다.

## [4.4.0] - 2026-07-21 (미발행)

### Added
- `render_preview` gains an additive optional `viewer` parameter (default
  `false`). When `true`, the manifest carries a self-contained scrollable
  document viewer under `structuredContent.viewer` — a sticky top bar, live
  page indicator, continuous scroll, and equations rendered as native MathML
  (with `python-hwpx[preview]`; the core chain fails closed to a LaTeX/script
  code block otherwise, never a silent drop). The viewer HTML is written to a
  workspace-guarded `viewer.html` and returned inline under a byte cap that
  degrades to path-only. `viewer.equationRendering` reports honest
  MathML/latex/script fallback counts. The parameter is orthogonal to
  rasterization; pair with `screenshot="off"` for the lightweight text path.
  Non-HWPX inputs and out-of-workspace writes are rejected by the existing
  guards. The contract hash moves to `c89cbc5f98eb5367`; the delta is proven
  additive-only (one tool, one optional input parameter plus one optional
  output field) in `docs/tool-contract-delta-4.4.0.json`.

### Internal
- Brings the five former `[dynamic-seam]` files (`ops_services/{form_fields,
  save_policy,tables,transactions}.py` and `workflow/service.py`) into the
  Pyright gate, typing-first: `DocumentStorage.save_document` gains
  the universal `quality` kwarg; the Local-only guarded exact-sidecar publication
  is narrowed to `LocalDocumentStorage` at the consumers; the table addressing
  kwargs use a `TypedDict`. Behavior is preserved. Only the 9 `[schema-frozen]`
  handlers remain excluded from Pyright.

## [4.3.2] - 2026-07-21

### Notes
- `v4.3.1`은 릴리스 게이트 실패로 미발행 보존 태그입니다(버전 고정 테스트 미갱신
  + CI 한정 스냅샷 정리 테스트 실패 — 조사 기록은 저장소 이슈/영수증 참조).
  PyPI/GitHub Release 산출물은 존재하지 않습니다.

## [4.3.1] - 2026-07-20 (미발행)

### Fixed
- workspace 루트가 설정되지 않았을 때의 cwd 폴백이 GUI MCP 클라이언트의
  퇴화 cwd(Windows Claude Desktop의 `C:\Windows\System32`, macOS의 `/`)를
  workspace로 삼아 — Windows에서는 모든 실제 경로가 거부되고 macOS에서는
  전체 파일시스템이 열리는 — 두 오동작을 모두 막습니다. 이제 퇴화 cwd가
  감지되면 `HWPX_MCP_WORKSPACE_ROOTS` 설정 예시를 담은 명확한
  `WORKSPACE_ROOT_INVALID` 오류를 반환하고, 서버는 기동을 유지해
  `mcp_server_health`로 원인을 확인할 수 있습니다. (#73, #77)


### Fixed
- The implicit cwd workspace fallback no longer silently adopts a degenerate
  current directory. GUI MCP clients launch the server from the filesystem root
  (`/` on macOS) or the Windows system directory (`C:\Windows\System32`), which
  made every real document path rejected as "outside the authorized workspace
  roots" (unusable for unconfigured Windows users) or — at the filesystem root —
  unbounded, defeating the fail-closed design. When neither
  `HWPX_MCP_WORKSPACE_ROOTS` nor the legacy `HWPX_MCP_SANDBOX_ROOT` is configured
  and the cwd is degenerate, `WorkspaceResolver.from_environment` now raises
  `WORKSPACE_ROOT_INVALID` with an actionable message naming
  `HWPX_MCP_WORKSPACE_ROOTS` and a short example. Explicit configuration
  behaviour is unchanged. (#73, refs #56)

### Changed
- An unconfigured degenerate cwd no longer crashes import or startup:
  `LocalDocumentStorage` defers the error so the server still boots,
  `mcp_server_health` reports the misconfiguration, and each document tool call
  returns the clean `WORKSPACE_ROOT_INVALID`. Explicit configuration errors
  still fail fast at startup.
- The README Claude Desktop / MCP client quickstart now sets
  `HWPX_MCP_WORKSPACE_ROOTS` from the start so new users avoid the cwd fallback.

## [4.3.0] - 2026-07-18

### Changed
- Promotes `apply_table_ops` and `apply_body_ops` from `compatibility` to
  `public`. Both are directly consumed as core primitives of the universal
  form-fill workflow, so the public classification states the truth; their
  `replacementTools` guidance is cleared. The installed surface stays exactly
  121 default / 132 advanced / 28 skill-required; baseline classification
  moves to 110 public / 9 compatibility. The contract hash moves to
  `f82caecbcfc742e9`; the delta is proven classification-only by payload
  substitution in `docs/tool-contract-delta-4.3.0.json`.
- Starts the tier-1 facade observation release: shipped skill guides now
  route `analyze_template_formfit` / `apply_template_formfit` /
  `fill_form_field` traffic to the canonical
  `analyze_form_fill`/`apply_form_fill`/`verify_form_fill` trio. The three
  tools stay registered and functional; demotion to `deprecated` is deferred
  to the next major after observing consumption.
- Raises the core floor to `python-hwpx>=3.3.1` (release-train alignment).

### Fixed
- `get_tables_by_handle` crashed with `AttributeError` on any document that
  contains a table, because it read the non-existent `table.columns`
  attribute; it now reads `column_count` and carries a regression test. Found
  by the Pyright ramp below.

### Internal
- Decomposes the worst-complexity handlers behaviour-preservingly:
  `_ensure_table_border_fill` (C901 57→≤10, via the private leaf
  `ops_services/_border_fill.py`), `apply_style_to_text_ranges` (48→≤10), and
  `matches` (29→split). The complexity ratchet baselines are lowered to match.
- Extends Pyright to the full mypy surface (8→22 of 36 files) with itemized
  exclusions: 9 `[schema-frozen]` handlers (implicit-Optional is the FastMCP
  schema source; explicit-Optional conversion belongs to a contract-changing
  release) and 5 `[dynamic-seam]` files pending a typed core seam.

## [4.2.1] - 2026-07-18

### Fixed
- Repoints the hermetic CI core checkout pin from the python-hwpx 3.2.0 release
  commit to the 3.3.0 release commit. The `v4.2.0` prepublish failed on this
  stale pin before publication, so no 4.2.0 PyPI artifact or GitHub Release
  exists; the tag is preserved as failure history and 4.2.1 is the actual
  public release. The contract hash moves to `fff2c9093ca4677b` because
  `MIN_MCP_VERSION` is part of the canonical payload; the tool surface stays
  exactly 121 default / 132 advanced / 28 skill-required.

### Note
- The feature content of 4.2.1 is identical to the unpublished 4.2.0 entry
  below.

## [4.2.0] - 2026-07-18 (tag preserved; not published)

### Changed
- Pins the MCP SDK exactly (`mcp==1.28.1`) so the package resolver admits the
  same set the runtime allowlist (`AUDITED_MCP_PATCHES`) admits: anything pip
  can install now also starts. Admitting a new SDK patch is an explicit
  re-audit documented in `docs/mcp-sdk-reaudit.md`.
- Removes the last package import cycle: the shared v2 render contracts
  (`RenderStatus`, `RenderJobV2`, `RenderReceiptV2`, `sign_submission`) moved
  to the leaf module `workflow/render_contracts.py`; `workflow/rendering` and
  `workflow/render_queue` re-export them unchanged. The architecture ratchet
  cycle baseline is now exactly zero, and a new SDK-import allowlist ratchet
  fails when any module outside the audited seams imports the `mcp` SDK.
- Optional real-Hancom verification now honors the core oracle controls:
  `HWPX_ORACLE_STRUCTURAL_ONLY=1` never enters GUI automation, and
  `HWPX_ORACLE_BUDGET_SECONDS` propagates one external deadline into every
  oracle subprocess timeout (python-hwpx >= 3.3.0 reachability probe applies).

### Fixed
- Workflow abstention receipts now carry the injected `tool_spec_hash` instead
  of the frozen released constant, keeping both receipt paths consistent when a
  divergent contract is injected.

## [4.1.0] - 2026-07-17

### Added
- Extends the existing `apply_document_commands` transaction to edit body text,
  table-cell text, and simple text in an existing section `BOTH` header in one
  revision-bound serialization. Dry-run, rollback, idempotency, byte preservation,
  reopen, and open-safety receipts remain part of the same installed tool.
- Adds architecture ratchets for handler ownership, service boundaries, import cycles,
  FastMCP private-access isolation, complexity, and the supported `mcp==1.28.1`
  registration/error/protocol contract.

### Changed
- Requires `python-hwpx>=3.2.0` for the multi-story transaction and correct
  renderable multi-section package construction. The `oracle` and `vision` extras
  require the matching `python-hwpx[visual]>=3.2.0` floor.
- Decomposes all 132 canonical callables into ten handler owners behind one immutable
  binding map, and places the exact `HwpxOps` compatibility facade over twelve bounded
  services. All unavoidable FastMCP private access is confined to one audited adapter.
- Preserves the exact 121 default / 132 advanced tool names, order, schemas, and 28
  skill-required tools. The approved minimum coordinates are MCP `4.1.0`, core `3.2.0`,
  and skill `0.4.0`; because those coordinates are part of the canonical payload, the
  release contract hash is `c127914cc3f4480e`.

### Fixed
- Routes durable workflow dispatch and direct FastMCP registration through the same
  immutable callable owners, preventing the two runtime paths from drifting.
- Fails the non-body story command closed for unsupported rich/control headers instead
  of partially editing or flattening their structure.

## [4.0.0] - 2026-07-16

### Added
- Adds one closed `hwpx.mixed-form-plan/v1` surface for native fields, label-adjacent
  cells, canonical paths, and exact-one body anchors. Analysis is non-mutating and
  apply reuses the core revision-bound transaction for dry-run, rollback, replay,
  byte-preservation, reopen, and open-safety receipts.
- Adds generated machine-readable input/output schemas, typed availability reasons,
  lifecycle/replacement metadata, and a narrow FastMCP adapter boundary.

### Changed
- Requires `python-hwpx>=3.1.0`, `mcp>=1.28.1,<1.29`, and `pydantic>=2.11,<3`.
  One ordered `ToolSpec` registry now validates
  registration, callables, signatures, schemas, profiles, health/capabilities,
  generated documentation, and the plugin contract.
- The transition surface is 121 default / 132 advanced tools with contract hash
  `f46ec677231b3a20`. Existing `hwpx.formfill.v1`, evalplan, native-field, and exam
  behavior remains explicit; five older planner tools are deprecated for one transition.

### Fixed
- Publishes and validates the source-owned `render_preview` manifest schema as
  `CallToolResult.structuredContent` while preserving inline image content blocks,
  instead of incorrectly advertising the outer MCP response envelope.
- Resolves postponed annotations without Python 3.10's implicit-`Optional`
  rewrite, keeping advertised input schemas and live argument validation identical
  across supported Python versions.
- Pre-reserves exact, randomly named recovery sidecars before transactional form and
  byte-preserving writes. Successful writes remove them after final identity checks;
  failed or raced writes retain the immutable preimages without overwriting an external
  winner, including publish-then-claim-loss and sparse backup-rotation cases.

### Removed
- Retires the pre-FastMCP 70-tool shadow registry and its unused legacy server,
  prompt handlers, schema sanitizer/builder, and logging bootstrap. Installed
  tools, schemas, health, capabilities, and documentation now come exclusively
  from the canonical `ToolSpec` registry and FastMCP entrypoint.

## [3.0.0] - 2026-07-16

### Removed
- Removes seven non-product QA tools and their bundled runtime from the public wheel. Supported
  exam, evaluation-plan, form-fill, authoring, editing, verification, workflow, and real-Hancom
  render surfaces remain public.

### Changed
- Requires `python-hwpx>=3.0.0` and exposes 126 default / 136 advanced product tools.
- Raises the matching skill floor to `0.2.0`; the intentional breaking delta, replacements,
  and zero-alias policy are recorded in [`docs/tool-contract-delta-3.0.0.json`](docs/tool-contract-delta-3.0.0.json)
  and [`docs/product-boundary-migration-3.0.0.md`](docs/product-boundary-migration-3.0.0.md).

## [2.23.1] - 2026-07-15

### Security
- Adds one fail-closed multi-root workspace resolver for relative, absolute, missing-output-parent,
  traversal, and symlink-escape cases. Hosts may provide `HWPX_MCP_WORKSPACE_ROOTS` as a JSON array;
  otherwise the intentional process working directory is the bounded single-root fallback.
- Promotes tool failures to redacted `hwpx.mcp-error/v1` JSON-RPC errors instead of successful results
  containing error text. Validation, permission, not-found, capability, document, network, and internal
  categories have stable MCP codes and retry guidance.
- Blocks outbound loopback, private, link-local, reserved, metadata, and redirect destinations by default.
  Intentional private render/storage networks require an explicit opt-in and link-local metadata stays denied.

### Changed
- Removes the unused `modelcontextprotocol` dependency and narrows ingest to
  `markitdown[pdf,docx,xlsx]>=0.1.6,<0.2`, matching the advertised input formats.
- Adds clean base/all-extra installation checks, public text/HWPX/wheel hygiene, Ruff `E9,F`, CodeQL,
  dependency review, Dependabot, immutable Action pins, and CycloneDX release SBOM generation.
- Makes source CI install the checked-out core explicitly and lets the public-wheel job wait for the newly
  released core to become visible on PyPI, avoiding a false failure during dependency-ordered releases.
- Requires `python-hwpx>=2.29.2` while preserving the exact 133 default / 143 advanced tool names.

## [2.23.0] - 2026-07-15

### Added
- **Typed agent document and blueprint surfaces**: compact semantic node/query/atomic-command tools plus typed
  `.hwpxbp` dump and strict atomic replay facades, sharing the core catalog, revision, fidelity, dependency,
  idempotency, rollback, lossless, and open-safety contracts.
- **Durable document workflows and rendering**: server-enforced workflow policy, authenticated durable Hancom
  render queue/transport, fixture visual-QA and guarded repair, blind benchmark receipts, and hardened
  non-product QA infrastructure.
- The exact release-facing ToolSpec expands to 133 default / 143 advanced tools.

### Fixed
- Retains the public 2.18.2 pathological-spacing repair for every touched replacement, paragraph insertion,
  addition, form fill, and table path while preserving legitimate compressed spacing and untouched source styles.
- Retains the public 2.18.3 SQUEEZE-cell safety through `python-hwpx>=2.29.1`; changed non-empty cells wrap with
  `BREAK`, while no-op, clear, and untouched cells preserve their original mode.
- Makes the release-facing `test` extra self-contained for visual fixture tests by installing Pillow and NumPy.
- Resolves the render-worker integration fixture from `PYTHON_HWPX_REPO` or the standard sibling checkout instead
  of a retired Stage-specific `python-hwpx-s067` worktree name, so GitHub CI exercises the current public core.

### Note
- Binds to the corrected public core release `2.29.1`; core `v2.29.0` was an immutable failed prepublish tag
  and did not produce a PyPI package or GitHub Release.
- 2.19.0–2.22.0 were staged local candidates rather than public releases; their accumulated changes are
  consolidated into this 2.23.0 public entry.

## [2.18.3] - 2026-07-14

### Fixed
- Prevent long values written into `lineWrap="SQUEEZE"` template cells from being compressed into unreadable overlapping glyphs. `apply_table_ops(fill_cell)` and regular table-cell edits now require `python-hwpx>=2.24.1`, which changes only touched non-empty cells to `lineWrap="BREAK"`; untouched/no-op/cleared cells retain their original wrap mode.

## [2.18.2] - 2026-07-13

### Fixed
- Prevent unreadable glyph over-print after text replacement, paragraph insertion, and table-cell fills when a template placeholder carries pathological character spacing (`hh:spacing <= -40`). Only touched runs are remapped to a deduplicated safe clone; the source character style and legitimate compressed spacing (for example `-37`) remain unchanged. Paragraph insertion now inherits the target neighbor instead of the unrelated section tail.

## [2.18.1] - 2026-07-10
### Fixed
- Restored the seven universal form-fill tools on the release-facing FastMCP entrypoint.
- Replaced legacy-union/count-based health with an exact ToolSpec contract shared by registration,
  capability reporting, generated skill API documentation, and installed-surface tests.
- Tightened core/MCP/plugin compatibility reporting and added protocol-level plugin smoke coverage.

## [2.18.0] - 2026-07-08
### Added
- **`describe_capabilities`**: task-oriented capability map for agents. Groups the ~150 flat tools into 16 domains (read·form-fill·author·edit·tables·styles·layout·toc-xref·pii·redline·exam·seal·generators·memo·verify·package) with intent + when-to-use + entry-point tools; `domain=` filters one group. A coverage drift-guard test asserts every registered tool is mapped (adding a tool without mapping it fails CI). Lets an external agent orient itself with one call instead of reading ~150 tool descriptions.


## [2.17.0] - 2026-07-08
### Added
- **Stage 3 universal form-fill tool surface**: `scan_form_guidance` (non-mutating form recon), `apply_body_ops` (byte-preserving body-paragraph ops incl. set_paragraph_text/strip/recolor, dryRun), `inspect_fill_residue` (fill residue zero-check gate). `apply_table_ops` gains `split_cell_vertical`·`clone_table`·`set_row_heights`·`set_cell_line_spacing` ops and `dryRun` transcript. Requires python-hwpx>=2.24.0.
### 비고
- Validated by producing a full 3학년 평가계획 from the blank form end-to-end (delete·reshape·fill·cleanup·recolor) with generic primitives only; real-Hancom render + owner review PASS.


## [2.16.0] - 2026-07-06
### Added
- **Document ingest gateway + Markdown-plan bridge (Spec 013)**: MCP surface to ingest external documents and bridge Markdown → `hwpx.document_plan` (`ingest_adapters`, `markdown_plan`).
### Fixed
- **Styled paragraph/table font size (양식 채우기 글자 크기)**: `add_paragraph` / `insert_paragraph` (and therefore `create_document_from_plan`) now apply the paragraph *style's* char property (`charPrIDRef`) to the text run instead of letting python-hwpx default it to `charPrIDRef="0"`. On templates whose char property #0 is a large title font — e.g. the KACE 투고양식, where #0 = 17pt (국문_제목) — styled body text no longer renders at that title size; it uses the style's real size (`j-본문` = 9pt). `add_table` cells get the document body (바탕글/Normal) char property for the same reason. A guard (`_enforce_run_char_pr`) re-asserts the style char property on freshly created runs and warns on an unexpected mismatch (regression detection). `add_heading` already passed `char_pr_id_ref`; this restores the same behaviour for body paragraphs and table cells.

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
- VisualComplete quality contract for general document saves: these writes use python-hwpx's `SavePipeline` and capability handshake, and their responses carry a `visualComplete` block (`ok`/`status`/`errorCodes`/`warnings`/`suggestedRetry`). (`byte_preserving_patch` is an explicit byte-preserving fast path: open-safety + capability gated, render gate N/A by design.)
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
