---
name: form-fill
description: Use this workflow when filling an existing HWPX across native fields, labeled cells, canonical paths, and body anchors through one analyzed, atomic, and verified mixed-form transaction.
---

# Form Fill

Use this workflow when a government, school, or public-sector form already exists and approved values must be applied across mixed locator types with minimal drift.

## Tool Order

1. Keep the approved source immutable and choose a distinct output path.
2. Read visible structure with `list_form_fields`, `get_document_outline`, `get_paragraphs_text`, and `get_table_map`.
3. Build one strict `hwpx.mixed-form-plan/v1` with the required `nativeField`, `labelCell`, `canonicalPath`, and `bodyAnchor` operations.
4. Call `analyze_form_fill(plan=...)` and resolve every ambiguity before applying.
5. Call `apply_form_fill(plan=compiledPlan)` so all operations commit once or roll back together.
6. Call `verify_form_fill(plan=compiledPlan, expected_output_revision=...)` and retain the unified receipt.
7. Keep `apply_evalplan_fill` as the dedicated evaluation-plan route and `compose_exam` as the separate exam route.
8. Use `fill_form_field`, `batch_replace`, or `set_table_cell_text` only for narrow retained compatibility cases.

## Minimize Layout Drift

- Keep table shapes unchanged unless the compiled plan explicitly permits a structural operation.
- Prefer existing fields and anchors instead of inserting paragraphs.
- Block on ambiguous or multi-match locators rather than selecting the first result.
- Require open-safety, byte-preservation, reopen, and privacy evidence before handoff.

## When To Inspect Package Parts

- The same visible label appears in multiple places and analysis cannot choose uniquely.
- The form contains unsupported drawing objects or opaque package structures.
- The canonical locator evidence is insufficient to authorize a write.

## Honest Limitations

- The canonical mixed-form route does not replace the dedicated evaluation-plan or exam workflows.
- Complex seals or unsupported drawings still require their dedicated tools or manual review.
- The source and output must remain distinct; never treat an in-place compatibility edit as the canonical transaction.
