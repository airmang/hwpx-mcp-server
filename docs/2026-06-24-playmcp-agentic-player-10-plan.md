# HWPX Document Agent for PlayMCP Implementation Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** Prepare `hwpx-mcp-server` as a PlayMCP / Agentic Player 10 submission-grade Remote MCP service for Korean HWPX document analysis, generation, validation, and safe editing.

**Architecture:** Keep the existing `hwpx-mcp-server` as the product core, but add a PlayMCP profile that exposes a small curated tool surface over Streamable HTTP. The PlayMCP profile must satisfy Kakao's schema/annotation/latency rules without weakening the existing local MCP workflow.

**Tech Stack:** Python 3.10+, `mcp>=1.14.1`, FastMCP Streamable HTTP, `uvicorn`, `python-hwpx>=2.11.1`, pytest.

---

## Contest Fit

**Submission concept:** HWPX Document Agent — an AI document specialist for Korean `.hwpx` files.

**User-facing pitch:** Upload or point to an HWPX document, then ask AI to summarize, inspect tables, validate official-document style, fill form fields, generate government-style reports, or safely create edited copies.

**Why this is strong:**

- HWPX is a Korea-specific document format with high public-sector, school, and enterprise relevance.
- Existing stack already has parsing, editing, generation, validation, repair, and Streamable HTTP smoke tests.
- PlayMCP wants useful Kakao Tools. HWPX document automation is a concrete tool, not a thin wrapper.

## PlayMCP Constraints Captured

From Kakao PlayMCP guide checked on 2026-06-24:

- Remote MCP only; public URL required.
- Streamable HTTP only.
- MCP protocol support range: `2025-03-26` through `2025-11-25`.
- Stateless MCP recommended.
- Tool/server name must not contain `kakao` in any position, case-insensitive.
- Tool count: 3-10 recommended, 20 maximum.
- Every tool must expose `name`, `description`, `inputSchema`, `annotations`.
- `annotations` must include `title`, `readOnlyHint`, `destructiveHint`, `openWorldHint`, `idempotentHint`.
- Tool descriptions should preferably be English, include the service name as a proper noun in English/Korean, and stay under 1024 chars.
- Result payloads must be minimal and cleaned; do not dump raw API/XML responses.
- Latency target: average <= 100ms, p99 <= 3000ms.

## Confirmed PlayMCP in KC Hosting Facts

Checked Kakao's public Notion guide on 2026-06-24:

- We do **not** need to operate our own public VM/server first.
- PlayMCP in KC is entered at `https://playmcp.kakaocloud.io`.
- It can create an MCP server from either:
  - Git source build: provide Git URL, branch/ref, Dockerfile path, optional PAT for private repos.
  - Container image: provide registry host/user/password if private, image name, image tag.
- For Git source build, the repository root or selected Dockerfile path must contain a `Dockerfile`.
- For container image registration, image architecture must be `linux/amd64`; Apple Silicon builds need `docker build --platform linux/amd64 ...`.
- After registration, status changes from `Starting` to `Active`.
- The active server detail page provides an `Endpoint URL`.
- That `Endpoint URL` is then copied into PlayMCP registration.
- PlayMCP in KC allows up to 2 MCP servers.

Implication: the required work is **not** buying/maintaining infrastructure. The required work is packaging this repo as a Dockerized Streamable HTTP MCP app that PlayMCP in KC can build/run and then registering the resulting endpoint.

## Current Code Anchors

Inspected files:

- `README.md`
  - Existing product positioning: local/stateless MCP server for reading, editing, inspecting, validating HWPX.
  - Existing stack statement: `python-hwpx`, `hwpx-mcp-server`, `hwpx-skill`.
- `pyproject.toml`
  - Package: `hwpx-mcp-server` version `2.4.1`.
  - Dependencies include `mcp>=1.14.1`, `uvicorn>=0.30`, `python-hwpx>=2.11.1`.
  - CLI entrypoint: `hwpx-mcp-server = hwpx_mcp_server.server:main`.
- `src/hwpx_mcp_server/server.py`
  - Uses `FastMCP("hwpx-mcp-server")`.
  - Already exposes many local tools via `@mcp.tool()`.
  - Already has Streamable HTTP app coverage through tests.
- `src/hwpx_mcp_server/tools.py`
  - `ToolDefinition.to_tool()` currently returns `name`, `description`, `inputSchema`, `outputSchema`; no explicit annotations in the inspected code path.
  - `HWPX_MCP_TOOLSET` already supports category filtering: `core`, `tables`, `styles`, `pipeline`, `debug`.
- `tests/test_streamable_http_transport.py`
  - Confirms `mcp.streamable_http_app()` is constructible.
  - Confirms key core tools exist.
- `tests/test_tool_schemas.py`
  - Already validates sanitized schemas and exposed tool definitions.

Repository state before this plan:

- `python-hwpx`: clean, `main...origin/main`, latest `8223b1f docs: VisualComplete plan v0.4 — oracle boundary is "Hancom reachable", not "Windows"`.
- `hwpx-mcp-server`: clean, `main...origin/main`, latest `30a450e fix: preserve generated document outlines`.
- `hwpx-plugins`: clean, `main...origin/main`, latest `1adf3f4 chore: release hwpx-skill plugin bundle 0.1.9`.

## Product Scope

### PlayMCP Submission Profile

Use product name:

- English: `HWPX Document Agent`
- Korean: `HWPX 문서 에이전트`

Do not rename the Python package. Add a PlayMCP-facing profile/metadata layer only.

### Curated Tool Surface

Expose 7 public PlayMCP tools. This stays inside Kakao's 3-10 recommendation and avoids overwhelming LLM tool choice.

1. `get_document_info`
   - Purpose: metadata, paragraph/table counts, basic document health.
   - Read-only: yes.
   - Idempotent: yes.
   - Open-world: false, unless document locator supports URL fetch.

2. `get_document_text`
   - Purpose: paginated clean text extraction for summary/Q&A.
   - Read-only: yes.
   - Idempotent: yes.

3. `get_table_map`
   - Purpose: table index, dimensions, caption/context, important cell preview.
   - Read-only: yes.
   - Idempotent: yes.

4. `find_text`
   - Purpose: exact/regex search with small surrounding context.
   - Read-only: yes.
   - Idempotent: yes.

5. `analyze_document_quality`
   - Purpose: official document/style/content hygiene score with gaps and repair hints.
   - Read-only: yes.
   - Idempotent: yes.

6. `create_document_from_plan`
   - Purpose: generate an HWPX document from a declarative plan.
   - Read-only: false.
   - Destructive: false if writing to a new output path only.
   - Idempotent: conditional; set false unless output naming is deterministic and overwrite is disabled.

7. `fill_template`
   - Purpose: fill form/template fields while preserving formatting and returning verification evidence.
   - Read-only: false.
   - Destructive: false if output path is separate from template.
   - Idempotent: conditional; set false.

Defer or hide from PlayMCP profile:

- Raw package XML tools: powerful but too debug-oriented.
- Broad edit tools: riskier and harder to explain to Kakao Tools users.
- Repair tools: useful but likely scary for first submission; keep as later upgrade.
- More than 10 tools: weakens tool-call quality.

## Non-goals

- Do not add Kakao-specific branding to server/tool names.
- Do not expose local filesystem arbitrary paths in PlayMCP production without sandboxing.
- Do not promise binary `.hwp` editing as the primary PlayMCP feature.
- Do not submit every existing MCP tool.
- Do not require Hancom Office for the base PlayMCP workflow.

## Risks

- Existing server exposes many tools; PlayMCP may reject or perform poorly unless profile filters are strict.
- Existing `ToolDefinition.to_tool()` inspected path lacks explicit MCP annotations; Kakao requires them.
- Remote deployment needs safe document input strategy. Raw local paths make sense locally, but not for a public Remote MCP.
- p99 <= 3000ms may fail for large files if extraction/generation is unrestricted.
- OAuth/custom header decision is unresolved. Public anonymous document processing may create abuse and privacy risk.

## Recommended Implementation Strategy

Strong recommendation: **add a PlayMCP profile in `hwpx-mcp-server`, not a new repo.**

Reason:

- Current repo already owns MCP transport and tools.
- `python-hwpx` should remain core library.
- `hwpx-plugins` should remain host-bundle/onboarding layer.
- PlayMCP is a deployment/product profile of the MCP server, not a separate engine.

---

## Task 1: Add PlayMCP profile contract document

**Objective:** Define the exact public tool surface, annotations, response shape, and deployment rules before implementation.

**Files:**

- Create: `docs/playmcp-profile.md`

**Step 1: Write the profile doc**

Include:

- Product name: `HWPX Document Agent / HWPX 문서 에이전트`.
- Public tool list: the 7 tools above.
- Hidden internal/debug tools.
- Annotation matrix for every public tool.
- Input policy: uploaded/remote document locator only, sandboxed temp storage, max file size.
- Output policy: cleaned JSON + short Markdown text, no raw XML dumps.
- Latency policy: pagination, max chars, max tables, timeout-friendly limits.

**Step 2: Verify doc exists**

Run:

```bash
python3 - <<'PY'
from pathlib import Path
p = Path('docs/playmcp-profile.md')
assert p.exists()
text = p.read_text()
for needle in ['HWPX Document Agent', 'get_document_info', 'annotations', 'latency']:
    assert needle in text
print('ok', p, len(text.splitlines()))
PY
```

Expected: `ok docs/playmcp-profile.md <line-count>`.

**Step 3: Commit**

```bash
git add docs/playmcp-profile.md
git commit -m "docs: define PlayMCP submission profile"
```

---

## Task 2: Add explicit tool annotations model

**Objective:** Make MCP tool definitions emit Kakao-required annotations.

**Files:**

- Modify: `src/hwpx_mcp_server/tools.py:1012-1027`
- Test: `tests/test_tool_schemas.py`

**Step 1: Write failing tests**

Add tests asserting every `ToolDefinition.to_tool()` result has annotations with:

- `title`
- `readOnlyHint`
- `destructiveHint`
- `openWorldHint`
- `idempotentHint`

Also assert `kakao` is not present in any tool name.

**Step 2: Run failure**

```bash
uv run pytest tests/test_tool_schemas.py::test_tool_definitions_include_playmcp_annotations -q
```

Expected: FAIL because annotations are missing.

**Step 3: Implement annotation fields**

Add fields to `ToolDefinition`:

```python
title: str | None = None
read_only: bool = True
destructive: bool = False
open_world: bool = False
idempotent: bool = True
```

Update `to_tool()` to pass `annotations=types.ToolAnnotations(...)` if the installed MCP type supports it. If SDK compatibility is uncertain, add a small helper that constructs `types.ToolAnnotations` and falls back safely only in non-PlayMCP mode.

**Step 4: Set conservative defaults**

- Read tools: readOnly true, destructive false, idempotent true.
- Generate/fill/edit tools: readOnly false, destructive false only when output-copy policy holds; otherwise destructive true.
- URL/document fetch tools: openWorld true only if they hit external URLs.

**Step 5: Run tests**

```bash
uv run pytest tests/test_tool_schemas.py tests/test_streamable_http_transport.py -q
```

Expected: PASS.

**Step 6: Commit**

```bash
git add src/hwpx_mcp_server/tools.py tests/test_tool_schemas.py
git commit -m "feat: add PlayMCP tool annotations"
```

---

## Task 3: Add `playmcp` curated toolset

**Objective:** Expose only 7 contest-facing tools when `HWPX_MCP_TOOLSET=playmcp` or `HWPX_MCP_PROFILE=playmcp` is set.

**Files:**

- Modify: `src/hwpx_mcp_server/tools.py`
- Modify: `src/hwpx_mcp_server/server.py` if `@mcp.tool()` direct registrations bypass `build_tool_definitions()`.
- Test: `tests/test_tool_schemas.py`
- Test: `tests/test_streamable_http_transport.py`

**Step 1: Write failing tests**

Test desired profile names exactly:

```python
EXPECTED_PLAYMCP_TOOLS = {
    'get_document_info',
    'get_document_text',
    'get_table_map',
    'find_text',
    'analyze_document_quality',
    'create_document_from_plan',
    'fill_template',
}
```

Assert no profile tool contains `kakao`.

**Step 2: Run failure**

```bash
HWPX_MCP_PROFILE=playmcp uv run pytest tests/test_tool_schemas.py::test_playmcp_profile_exposes_curated_tools_only -q
```

Expected: FAIL because profile does not exist yet.

**Step 3: Implement profile filtering**

Options:

- Preferred: `HWPX_MCP_PROFILE=playmcp` maps to a named allowlist and friendly aliases.
- Keep existing `HWPX_MCP_TOOLSET` categories for local workflows.
- If current FastMCP direct decorators expose too much, add a dedicated app factory for PlayMCP instead of mutating global `mcp`.

**Step 4: Add aliases if needed**

Existing local names may differ. Keep existing names stable for local clients. Add PlayMCP aliases only in profile mode:

- `open_info` or `get_document_info` equivalent -> `get_document_info`
- `read_text` or `get_document_text` equivalent -> `get_document_text`
- existing table map equivalent -> `get_table_map`
- existing find equivalent -> `find_text`

**Step 5: Run tests**

```bash
HWPX_MCP_PROFILE=playmcp uv run pytest tests/test_tool_schemas.py tests/test_streamable_http_transport.py -q
uv run pytest tests/test_tool_schemas.py tests/test_streamable_http_transport.py -q
```

Expected: both profile and default tests pass.

**Step 6: Commit**

```bash
git add src/hwpx_mcp_server/tools.py src/hwpx_mcp_server/server.py tests/test_tool_schemas.py tests/test_streamable_http_transport.py
git commit -m "feat: add PlayMCP curated tool profile"
```

---

## Task 4: Add public Remote MCP input policy

**Objective:** Make PlayMCP-safe document access explicit and sandboxed.

**Files:**

- Modify: `src/hwpx_mcp_server/core/locator.py`
- Modify: `src/hwpx_mcp_server/storage.py` or the current document loading boundary.
- Test: add or update locator/storage tests.

**Step 1: Write failing tests**

Tests:

- Absolute paths outside sandbox are rejected in PlayMCP profile.
- Remote URL fetch requires allowlisted schemes: `https` only.
- Max source size is enforced before full parse.
- Error responses are cleaned and do not expose server filesystem paths.

**Step 2: Run failure**

```bash
HWPX_MCP_PROFILE=playmcp uv run pytest tests/test_http_storage.py tests/test_locator_models.py -q
```

Expected: FAIL until PlayMCP policy is implemented.

**Step 3: Implement policy**

Add env/config defaults:

- `HWPX_MCP_PROFILE=playmcp`
- `HWPX_MCP_SANDBOX_ROOT=/tmp/hwpx-playmcp`
- `HWPX_MCP_MAX_SOURCE_SIZE=10485760` initially, adjust after benchmarks.
- `HWPX_MCP_ALLOW_REMOTE_DOCUMENTS=1`
- `HWPX_MCP_ALLOWED_SCHEMES=https`

**Step 4: Run tests**

```bash
HWPX_MCP_PROFILE=playmcp uv run pytest tests/test_http_storage.py tests/test_locator_models.py tests/test_streamable_http_transport.py -q
```

Expected: PASS.

**Step 5: Commit**

```bash
git add src/hwpx_mcp_server tests
git commit -m "feat: harden PlayMCP document input policy"
```

---

## Task 5: Add latency and response-size guards

**Objective:** Keep PlayMCP responses small and p99-safe.

**Files:**

- Modify: `src/hwpx_mcp_server/utils/helpers.py`
- Modify: tool implementations that return large text/table payloads.
- Test: relevant read/search/table tests.

**Step 1: Write failing tests**

Tests:

- `get_document_text` returns a chunk with `nextOffset` rather than full giant text.
- `get_table_map` caps cell previews.
- Quality analysis returns `status`, `gaps`, `repairHints`, not full raw internals.
- Tool result JSON stays below a configured byte threshold for fixtures.

**Step 2: Run failure**

```bash
HWPX_MCP_PROFILE=playmcp uv run pytest tests/test_read_export_tools.py tests/test_hwpx_ops.py -q
```

Expected: FAIL until profile limits are applied.

**Step 3: Implement limits**

Add PlayMCP defaults:

- Text chunk limit: 4000 chars.
- Search max results: 20.
- Context radius: 80 chars.
- Table cell preview: 120 chars.
- Max table count returned in map: 20.
- Include `truncated: true` and next-action hints when capped.

**Step 4: Run tests**

```bash
HWPX_MCP_PROFILE=playmcp uv run pytest tests/test_read_export_tools.py tests/test_search.py tests/test_hwpx_ops.py -q
```

Expected: PASS.

**Step 5: Commit**

```bash
git add src/hwpx_mcp_server tests
git commit -m "feat: cap PlayMCP tool responses"
```

---

## Task 6: Add PlayMCP in KC Docker launch path

**Objective:** Provide a Dockerized Streamable HTTP app that PlayMCP in KC can build/run from Git source or register from a `linux/amd64` container image. We do not operate a separate public server; PlayMCP in KC runs the container and gives us the Endpoint URL.

**Files:**

- Create: `Dockerfile`
- Modify: `README.md`
- Create: `docs/playmcp-deployment.md`
- Optional create: `.dockerignore`

**Step 1: Verify current CLI launch path**

Current inspected entrypoint already supports Streamable HTTP in `src/hwpx_mcp_server/server.py`:

```bash
HWPX_MCP_PROFILE=playmcp hwpx-mcp-server --transport streamable-http --host 0.0.0.0 --port 8000
```

So this task should focus on Docker packaging unless tests prove the CLI path is broken.

**Step 2: Run failure**

```bash
HWPX_MCP_PROFILE=playmcp uv run pytest tests/test_streamable_http_transport.py -q
```

Expected: FAIL if CLI path is incomplete.

**Step 3: Implement Docker packaging**

Add a root `Dockerfile` because PlayMCP in KC Git source build expects a Dockerfile at the repository root unless another path is specified.

Container requirements:

- Build/run on `linux/amd64`.
- Install the package and its dependency `python-hwpx` inside the image.
- Expose port `8000`.
- Start with:

```bash
HWPX_MCP_PROFILE=playmcp HWPX_MCP_TRANSPORT=streamable-http HWPX_MCP_HOST=0.0.0.0 HWPX_MCP_PORT=8000 hwpx-mcp-server --transport streamable-http --host 0.0.0.0 --port 8000
```

- Set a sandbox root under `/tmp`, not the repository path.
- Do not require any external VM.

**Step 4: Document deployment**

`docs/playmcp-deployment.md` must include:

- Git source deployment path.
- Container deployment path.
- Required env vars.
- Health check.
- MCP Inspector verification command.
- PlayMCP registration checklist.

**Step 5: Run tests**

```bash
HWPX_MCP_PROFILE=playmcp uv run pytest tests/test_streamable_http_transport.py tests/test_tool_schemas.py -q
```

Expected: PASS.

**Step 6: Commit**

```bash
git add Dockerfile .dockerignore README.md docs/playmcp-deployment.md tests
git commit -m "feat: add PlayMCP in KC Docker launch path"
```

---

## Task 7: Add PlayMCP submission materials

**Objective:** Prepare text needed for PlayMCP registration and Agentic Player 10 preliminary form.

**Files:**

- Create: `docs/playmcp-submission.md`

**Step 1: Write registration copy**

Include:

- MCP name.
- Short Korean description.
- Short English description.
- Tool descriptions under 1024 chars.
- Example user prompts.
- Data/privacy note.
- Known limitations.

**Step 2: Add example prompts**

Examples:

- `이 HWPX 공문에서 결재/시행/본문 요지를 요약해줘.`
- `문서 안의 표 목록과 각 표의 핵심 값을 정리해줘.`
- `보고서 문체와 공식문서 형식 문제를 점검해줘.`
- `이 계획 JSON으로 HWPX 보고서 초안을 만들어줘.`

**Step 3: Verify no forbidden branding in names**

```bash
python3 - <<'PY'
from pathlib import Path
text = Path('docs/playmcp-submission.md').read_text().lower()
for line in text.splitlines():
    if line.startswith(('mcp name:', 'tool name:', '- tool:')):
        assert 'kakao' not in line
print('ok submission copy')
PY
```

Expected: `ok submission copy`.

**Step 4: Commit**

```bash
git add docs/playmcp-submission.md
git commit -m "docs: add PlayMCP submission copy"
```

---

## Task 8: End-to-end Inspector and deployment verification

**Objective:** Prove the PlayMCP profile is actually submission-ready.

**Files:**

- Create: `docs/playmcp-verification-report.md`

**Step 1: Run schema tests**

```bash
HWPX_MCP_PROFILE=playmcp uv run pytest tests/test_tool_schemas.py tests/test_streamable_http_transport.py -q
```

Expected: PASS.

**Step 2: Run MCP Inspector**

Use the official MCP Inspector against the Streamable HTTP endpoint after deployment.

Record:

- Endpoint URL.
- Protocol version negotiated.
- Tool count.
- Tool names.
- Annotation presence.
- Sample calls and response time.

**Step 3: Run PlayMCP smoke scenario**

Scenario:

1. Call health/tool guide.
2. Analyze a small public-safe HWPX fixture.
3. Extract text.
4. Extract table map.
5. Run quality analysis.
6. Generate a new HWPX from a small document plan.

**Step 4: Record evidence**

`docs/playmcp-verification-report.md` must include:

- Test command outputs.
- Inspector result summary.
- Latency samples.
- Known residual risks.
- Go/no-go decision.

**Step 5: Commit**

```bash
git add docs/playmcp-verification-report.md
git commit -m "docs: record PlayMCP verification evidence"
```

---

## Final Submission Checklist

Before Kakao submission:

- [ ] Public endpoint deployed through PlayMCP in KC.
- [ ] MCP Inspector passes.
- [ ] PlayMCP `정보 불러오기` succeeds.
- [ ] Tool count is 7 in PlayMCP profile.
- [ ] Every tool has required annotations.
- [ ] No server/tool name contains `kakao`.
- [ ] All descriptions include `HWPX Document Agent(HWPX 문서 에이전트)` where natural.
- [ ] Tool results are capped and clean.
- [ ] Health report exposes profile and dependency versions.
- [ ] AI 채팅에서 5 smoke prompts pass.
- [ ] 심사 요청 before 2026-07-14.
- [ ] 심사 승인 후 전체 공개.
- [ ] 공개 MCP URL copied into Player 예선 참여 form.

## Execution Policy

Forte should not directly perform broad implementation inside this repo unless 마스터 explicitly overrides the Code PM execution boundary. Recommended execution path:

1. Forte owns design, plan, verification, and submission readiness review.
2. Implementation tasks go to Codex/OpenCode/Claude Code through orchestration.
3. Forte verifies diffs, tests, PlayMCP constraints, and final submission copy.

## Strong Recommendation

Start with Tasks 1-3 only. That is the critical cut: profile doc, annotations, curated toolset. If those land cleanly, deployment and submission copy are straightforward. If those fight the current FastMCP registration model, stop and add a dedicated PlayMCP app factory rather than bending the global local MCP surface.
