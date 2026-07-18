# 5.0.0 deprecated-stub removal plan

Five transition stubs remain callable in every 4.x release. They are
`ToolClassification.DEPRECATED` in the canonical registry, each with tested
replacement guidance, and the architecture/contract gates keep them from
drifting. **They will be removed at the next major boundary (5.0.0)** — never
in a 4.x minor/patch. This document is the migration contract for that removal;
authored in S-082 while the stubs remain fully functional.

## The five stubs and their replacements

| Deprecated tool | Replace with | Migration note |
|---|---|---|
| `plan_edit` | `apply_document_commands` | The plan step is subsumed: build the command batch directly; `dryRun` gives the former plan/preview semantics with revision binding. |
| `preview_edit` | `apply_document_commands` | Use `dryRun: true` — same receipt shape, plus rollback/idempotency guarantees the old preview lacked. |
| `apply_edit` | `apply_document_commands` | One atomic, revision-bound batch replaces the single-edit call; pass one command. |
| `analyze_quality_generation` | `create_document_from_plan` + `inspect_document_quality` | Analysis is a read of the plan schema plus the quality inspector; no separate pre-analysis pass is needed. |
| `apply_quality_generation` | `create_document_from_plan` (+ `create_proposal_document` for proposal presets) | Plan-driven generation carries the quality policy directly. |

## Removal checklist (execute at 5.0.0, one release)

1. Remove the five ToolSpecs and their handler bindings; counts change
   132 → 127 advanced (verify the default count delta from the registry, do
   not assume). No aliases, no ghost wrappers — S-078 policy.
2. Regenerate the contract (`render_tool_contract.py`), pin the new
   `RELEASED_CONTRACT_HASH`, and author `tool-contract-delta-5.0.0.json` with
   the removed names and this document as the migration reference
   (`render_contract_delta.py` gains a new frozen receipt; the 4.0.0 receipt
   stays frozen).
3. Sweep shipped skill guidance: the S-082 census found the stub names in the
   skill bundle guidance and eval tasks (see workspace
   `docs/2026-07-18-facade-decision-table.md`); every reference must migrate
   to the replacement tools in the same release.
4. Update `tests/test_tool_contract.py` classification counts
   (deprecated 5 → 0) and the `render_contract_delta.py` structural checks
   that assert "five transition deprecations".
5. Bundle a 5.0.0 migration section in the skill CHANGELOG pointing here.

## What must NOT ride along

Compatibility facades (11, `ToolClassification.COMPATIBILITY`) are a separate
decision with a separate evidence bar — the S-082 census shows all of them
actively consumed. Facade reduction requires guidance migration and usage
evidence first; do not batch it into the stub removal by default.
