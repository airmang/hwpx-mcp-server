## [6.0.4] - 2026-07-28

`v6.0.3` published the canonical `python-hwpx-automation` 6.0.3 but the
compatibility upload was rejected (the `hwpx-mcp-server` project's
trusted publisher did not yet accept the renamed repository). Per the
partial-publish rule the canonical 6.0.3 release is preserved and the
train recovers as 6.0.4 so the exact canonical==compat version lock
holds. Never delete, move, or reuse `v6.0.3`.
