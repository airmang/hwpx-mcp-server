# MCP Unit Audit Report

Stage: S-039

## Policy

- Public tool inputs should use human units: points for font size, millimeters
  or point strings for borders, percentages for ratio-style report values.
- File size and archive safety limits remain bytes because they describe
  transport/storage limits.
- HWP units are allowed inside implementation code and returned diagnostics,
  but should not be required from normal agent-facing tool calls.

## Current Public Inputs Reviewed

- `format_text.font_size`: points. Docstring updated.
- `create_custom_style.font_size`: points. Docstring updated.
- `AddTableInput.borderWidth`: accepts human string values such as `0.1 mm`
  or `1 pt`; schema description updated.
- `SetTableBorderFillInput.borderWidth`: accepts human string values such as
  `0.1 mm` or `1 pt`; schema description updated.
- `repair_hwpx.maxEntrySize`, `maxTotalSize`, `maxSourceSize`: bytes by design.
- Table auto-fit width calculations: internal HWP units only, not exposed as
  required user input.

## Follow-Up Candidates

- When S-043 adds paragraph/page formatting tools, expose page and margin values
  as `*_mm`, line spacing as percent, and font sizes as pt.
- Avoid adding new raw `hwpunit` input fields unless the field name explicitly
  says `hwpUnit` and there is a human-unit alternative.
