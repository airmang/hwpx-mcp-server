# Canonical agent runtime owner

`hwpx_automation.office.agent` is the canonical application-layer owner of the
semantic agent runtime. It was transplanted from the prior core compatibility
line at `python-hwpx` commit
`fd879637f97a796c7a038e2c0c4a647a9b501064`:

- 19 Python files;
- 10,024 LOC;
- the same internal relative-package structure;
- one ownership import change in `model.py`:
  `hwpx.mutation_report` replaces the former core-package relative import;
- one compatibility-only presentation normalization in `cli.py`: fixed
  80-column help formatting and normalized choice-list quoting keep public CLI
  output byte-stable across supported Python minors.

All other Python-source differences from the transplant baseline are
prohibited. Wire identifiers such as `hwpx.agent-batch/v1`, the CLI program
name `hwpx`, JSON schemas, results, errors, and exit codes are compatibility
contracts and do not change with the Python owner namespace.

MCP production traffic enters this owner through:

- `hwpx_automation.agent_document`;
- `hwpx_automation.mixed_form`;
- `hwpx_automation.runtime`.

The product-boundary gate rejects any production import from the frozen core
`hwpx.agent` copy and limits the canonical runtime to the approved public core
seams: document, OXML, mutation report, quality, table targeting, and package
open-safety validation.

The prior core 4.x copy was compatibility-only and accepted no feature work.
The 5.0 boundary removes that application runtime from core; the canonical
automation owner above is now the only implementation. Generic HWPX primitives
remain in core under their own stable contracts.
