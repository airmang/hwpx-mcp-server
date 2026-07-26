# Compatibility and deprecation observation

The current public 5.1 installation has **119 default / 127 advanced / 28
skill-required** tools at contract hash `429cb6706323e762`. The unreleased 6.0
source candidate keeps those counts and moves the contract hash to
`0ce938371f0b55a6` for the 5.0/6.0/1.0 floors and canonical automation identity.
This observation does not remove or rename a tool, parameter, output field, or
error code.

The public observation runs from 2026-07-24 through 2026-10-31
(Asia/Seoul), for at least 90 days. Existing callers can report the tool name,
client, payload shape, and expected behaviour in
[the compatibility observation issue](https://github.com/airmang/hwpx-mcp-server/issues/88).
The old URL is historical public issue provenance; the repository rename must
preserve it through GitHub redirect.

## Decision at the opening census

All six compatibility tools and all three deprecated tools remain installed and
functional. The opening recommendation is **extend** for every surface; there
are no removal recommendations.

| Classification | Retained tool | Canonical route for new work | Decision |
|---|---|---|---|
| compatibility | `apply_edits` | `apply_document_commands` | extend |
| compatibility | `apply_evalplan_fill` | dedicated eval-plan route; generic forms use `analyze_form_fill` → `apply_form_fill` → `verify_form_fill` | extend |
| compatibility | `create_comparison_table_document` | `create_document_from_plan` | extend |
| compatibility | `create_government_report_document` | `create_document_from_plan` | extend |
| compatibility | `create_proposal_document` | `create_document_from_plan` | extend |
| compatibility | `fill_by_path` | `analyze_form_fill` → `apply_form_fill` → `verify_form_fill` | extend |
| deprecated | `analyze_template_formfit` | `analyze_form_fill` → `apply_form_fill` → `verify_form_fill` | extend |
| deprecated | `apply_template_formfit` | `analyze_form_fill` → `apply_form_fill` → `verify_form_fill` | extend |
| deprecated | `fill_form_field` | `analyze_form_fill` → `apply_form_fill` → `verify_form_fill` | extend |

`extend` means “retain compatibility while collecting real usage”, not
“prefer this route for new code”. Deprecation guidance stays visible, but the
observation is not authorization to remove a tool at the next release.

## Core 4.x compatibility

Application runtime ownership has moved into these automation modules:

- `office.agent`
- `office.authoring`
- `office.compliance`, `office.quality`, and `office.utilities`
- `office.form_fill`
- `office.evalplan`
- `office.exam`
- `office.rendering`
- `office.document_ops`

The corresponding `python-hwpx` 4.x imports, three console entry points, and
published schema/report versions remain compatible. Reusable HWPX structure,
OXML, mutation, and deterministic algorithm contracts remain core library
responsibilities. The full core policy is documented in
[`python-hwpx`'s 4.x compatibility observation](https://airmang.github.io/python-hwpx/compatibility-observation-4.x.html).

During 4.x, a compatibility mirror may receive security or correctness fixes
only when the canonical runtime and mirror have a parity test and evidence
receipt. New application workflow features belong to the automation owner;
FastMCP remains an optional adapter over that owner.

## Migration and rollback

Migrate one route at a time:

1. Record the current request and its structured result or failure envelope.
2. Run the canonical route with the same fixture in a clean installation.
3. Compare semantic output, rollback behaviour, error codes, and open-safety
   evidence—not incidental JSON ordering.
4. Switch the caller only after parity passes.
5. If a client integration fails, point that caller back to the retained tool.
   The old tool is still present, so rollback does not require downgrading the
   automation application/MCP adapter or changing the contract hash.

Client problems are not evidence that the tool is unused. Public reports have
already shown that GUI working directories, workspace configuration, tool names,
and schema presentation affect adoption independently of server behaviour.

## What happens after 2026-10-31

Nothing is removed automatically. The closing census must make an evidence-backed
keep/remove/extend decision for each tool, core import family, CLI,
schema/report version, and documented workflow. Any removal still requires a
separately approved next-major change, a migration table, a fresh installed
protocol test, and a verified rollback path.
