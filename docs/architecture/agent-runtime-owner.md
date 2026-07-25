# Canonical agent runtime owner

`hwpx_automation.office.agent` is the canonical application-layer owner of the
released semantic agent runtime as of S-097. It was transplanted from
`python-hwpx` commit `fd879637f97a796c7a038e2c0c4a647a9b501064`:

- 19 Python files;
- 10,008 LOC;
- the same internal relative-package structure;
- one ownership import change in `model.py`:
  `hwpx.mutation_report` replaces the former core-package relative import.

All other Python-source differences are prohibited during the initial
transplant. Wire identifiers such as `hwpx.agent-batch/v1`, the CLI program
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

The core 4.x copy remains operational for Python imports and the published
`hwpx` console entry point. It accepts no feature work. Security or correctness
fixes originate here, gain a parity regression, and are mirrored to core only
with an explicit compatibility receipt. Removal belongs to a separately
approved core-major Stage.
