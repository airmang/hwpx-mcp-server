# Phase 4 Scope Pivot: No New Public MCP Tools By Default

## Summary

This PR does not add new public MCP tools.

It narrows the Phase 4 direction so the repo keeps:

- `python-hwpx` as the upstream engine layer
- the existing FastMCP surface as the stable product API
- reference-preserving workflows primarily in orchestration/workflow guidance unless a truly missing engine capability is proven

## Why

The earlier Phase 4 direction risked widening the public MCP surface around workflow-heavy or heuristic concepts before the underlying engine semantics were stable.

That would make the downstream MCP layer harder to evolve and easier to drift from `python-hwpx`.

This pass instead classifies the proposed Phase 4 ideas and records where each one belongs.

## Classification

| Proposed tool | Bucket | Decision |
|---|---|---|
| `validate_hwpx_package` | already covered by an existing MCP tool | Use `validate_structure` together with `package_parts`, `package_get_xml`, and `package_get_text`; do not add a new alias. |
| `extract_reference_parts` | better expressed as a workflow/skill instruction | Keep this as orchestration guidance built from existing package-inspection tools. |
| `compare_reference_structure` | better implemented upstream in `python-hwpx` | A robust structure diff should be an engine capability first, not a downstream MCP invention. |
| `layout_drift_report` | better kept as an internal helper, not a public MCP tool | Any near-term drift report would be heuristic and should not become part of the stable public MCP contract yet. |

Current conclusion:

- none of the four candidates currently belongs in the "truly worth exposing as a future public MCP tool" bucket

## What Changed

- updated [`docs/upstream-audit.md`](docs/upstream-audit.md) with the Phase 4 scope-pivot classification and downstream/upstream boundary rationale
- updated [`docs/follow-up-roadmap.md`](docs/follow-up-roadmap.md) to replace the old public-tool expansion plan with a no-new-public-tools default
- kept the current public MCP surface unchanged

## Existing MCP Coverage To Prefer

- `validate_structure`
- `package_parts`
- `package_get_xml`
- `package_get_text`
- `hwpx_extract_json`
- `object_find_by_tag`
- `object_find_by_attr`

These tools already provide the raw package/document inspection surface needed for most reference-preserving orchestration patterns.

## Tests Run

```bash
python -m pytest -q tests/test_mcp_end_to_end.py tests/test_advanced.py tests/test_contract.py
```

Result:

- `14 passed`

## Follow-up

- document reference-preserving workflow patterns using the current MCP tools
- record any truly missing package/structure comparison primitives as upstream `python-hwpx` needs first
- revisit a public MCP addition only if existing-tool orchestration proves clearly insufficient
