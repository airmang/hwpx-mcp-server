# HWPX Stack Release Handoff - 2026-05-30

Stage: hwpx display S-003 / server S-006

## Verification

- python-hwpx: `uv run --with pytest python -m pytest -q` passed with 277 passed, 2 skipped, 1 warning.
- hwpx-mcp-server: `uv run --with pytest python -m pytest -q` passed.
- hwpx-skill: `uv run --with lxml --with /Users/wilycastle/Code/projects/hwpx/python-hwpx python scripts/quickcheck.py --document-plan --operating-plan --template-formfit --visual-review` passed.

## Handoff State

- document-plan, template-formfit, operating-plan file-only quality, and visual-review handoff wording is aligned across the three repos.
- `visual_review_required=true` remains a final-submission gate, not a structural quality failure.
- The quickcheck visual-review path records blocked fallback evidence when no HWPX viewer is available.

## Residual Release Notes

- python-hwpx is ahead of its remote by one commit and still has unrelated dirty docs/OPC/example/lockfile work.
- hwpx-mcp-server is ahead of its remote and still has unrelated dirty README, docs, egg-info, generated work, status, and lockfile paths.
- hwpx-skill is ahead of its remote by two commits and still has `.omx/` plus `examples/05_mcp_quality_pipeline.md` untracked.
