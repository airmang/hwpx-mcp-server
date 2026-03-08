# Phase 5: Skill-First Workflows on the Existing MCP Surface

## Summary

This PR keeps the existing FastMCP tool inventory as the stable product surface and adds a skill-first workflow layer around it.

It does not add new public MCP tools.

Instead, it documents how to orchestrate the current surface for:

- reference-preserving edit
- government/public-form filling
- template-based document generation
- cautious edit-review-save flows

## Why

`python-hwpx` remains the upstream engine layer.

`hwpx-mcp-server` remains the downstream MCP product surface.

Reference-preserving workflows are mostly orchestration problems on top of existing tools unless a missing engine capability is clearly proven.

## What Changed

### New workflow guide

- Added `docs/skill-first-workflows.md`
- Mapped the current MCP surface to workflow needs:
  - package part reads
  - XML/text extraction
  - structure validation
  - plan/review pipeline
  - template-fill composition
  - copy/save behavior

### New thin example skills

- Added `examples/skills/reference-preserving-edit/SKILL.md`
- Added `examples/skills/form-fill/SKILL.md`
- Added `examples/skills/template-generation/SKILL.md`

These skills stay thin and orchestration-focused:

- when to use the workflow
- which existing MCP tools to call and in what order
- how to minimize layout drift
- when to inspect package parts or validate
- honest limitations

### Docs updates

- Updated `README.md` with a new skill-first workflow section and links
- Updated `docs/use-cases.md` with workflow-guide references and current surface boundaries
- Updated `docs/follow-up-roadmap.md` to keep Phase 5 scope narrow and public-tool growth gated

## Explicit Surface Boundaries

- No new public MCP tools were added.
- There is still no active public `fill_template` tool on the FastMCP surface.
- There is still no active public `save_as` tool on the FastMCP surface.
- Mutating tools still persist immediately via the shared atomic save path.
- `plan_edit`, `preview_edit`, and `apply_edit` remain review/verification pipeline tools, not a general HWPX patch engine.

## Prompt/Resource Layer

No new prompt or resource layer was added.

Reason:

- the authoritative product surface is still `src/hwpx_mcp_server/server.py`
- legacy prompt/resource code exists, but reviving it here would broaden scope and risk tool-surface drift again

## Testing

Focused active-surface regression run:

```bash
python -m pytest tests/test_mcp_end_to_end.py tests/test_advanced.py tests/test_contract.py -q
```

## Remaining Gaps

- There is still no public structure-diff or layout-drift report tool.
- Rich reference-structure comparison still belongs upstream in `python-hwpx` first.
- Workflow review/save remains copy-first because the active surface has immediate persistence semantics.
