# Agent runtime parity corpus

These 15 pytest modules are the executable 4.x compatibility corpus copied from
`python-hwpx` commit `fd879637f97a796c7a038e2c0c4a647a9b501064`.
Only the import owner prefix changes:

```text
hwpx.agent -> hwpx_mcp_server.office.agent
```

The original core modules remain in place and pass 236 tests against the frozen
compatibility copy. This directory runs the same 236 tests against the MCP
canonical owner. Required fixture bytes are copied without modification.

The core-only console-entry-point assertion remains in the core corpus. Its MCP
counterpart asserts the canonical parser still advertises the wire-compatible
program name `hwpx`; MCP does not add or steal the core 4.x console script.
