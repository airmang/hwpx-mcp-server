# Contributing

Use Python 3.12 for the release-equivalent environment. In a checkout beside
`python-hwpx`, create an isolated environment and run:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e ../python-hwpx -e ".[test]"
python scripts/check_public_hygiene.py
ruff check --select E9,F .
pytest -q
```

To test against a different core checkout, set `PYTHON_HWPX_REPO` explicitly.
Changes to tool names, schemas, defaults, or counts must update the generated
tool contract and the plugin repository in the same release sequence.

Never commit real user documents, private evidence, credentials, workstation
paths, or generated operational reports. Use minimal synthetic HWPX fixtures and
document why each fixture is required. Report vulnerabilities through
[SECURITY.md](SECURITY.md), not a public issue.
