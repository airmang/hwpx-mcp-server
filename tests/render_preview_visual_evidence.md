# Render Preview Visual Evidence

Stage: S-040

## Corpus Liveness

- Current pinned hwpxlib fixture manifest:
  `python-hwpx/tests/fixtures/hwpxlib_corpus/manifest.json`
- Pinned ref: `3bbaaa90bdb1f14c58fd2f87c80105bc8fd37473`
- Manifest count in this checkout: 47 samples
- Crash check: `python-hwpx .venv/bin/python -m pytest -q tests/test_layout_preview.py`
  renders every manifest sample through `render_layout_preview()`.

The Stage brief mentions 49 samples, but the checked-in pinned manifest for this
workspace contains 47. The no-crash claim is therefore for all currently
vendored samples.

## Chrome Headless Sample Evidence

Command shape:

```bash
cd /Users/wilycastle/Code/projects/hwpx/hwpx-mcp-server
.venv/bin/python - <<'PY'
from pathlib import Path
from tempfile import TemporaryDirectory
from hwpx_mcp_server.server import render_preview

root = Path('/Users/wilycastle/Code/projects/hwpx/python-hwpx/tests/fixtures/hwpxlib_corpus')
samples = [
    'reader_writer__PageSize_Margin.hwpx',
    'reader_writer__SimpleTable.hwpx',
    'reader_writer__HeaderFooter.hwpx',
    'reader_writer__MultiColumn.hwpx',
    'reader_writer__SimplePicture.hwpx',
    'reader_writer__PageFunctions.hwpx',
    'reader_writer__ChangeTrack.hwpx',
    'reader_writer__sample1.hwpx',
    'tool__textextractor__Table.hwpx',
    'error__20250523__프로젝트 계획서.hwpx',
]
with TemporaryDirectory() as td:
    out_root = Path(td)
    for index, sample in enumerate(samples, 1):
        result = render_preview(
            str(root / sample),
            output_dir=str(out_root / f'{index:02d}'),
            mode='pages',
            screenshot='require',
            max_pages=1,
        )
        print(index, sample, result['status'], result['screenshotEngine']['backend'])
PY
```

Observed result on 2026-06-11 KST:

| # | sample | status | backend | pages | screenshots |
|---|---|---|---|---:|---:|
| 1 | `reader_writer__PageSize_Margin.hwpx` | ok | chrome-headless-cli | 1 | 1 |
| 2 | `reader_writer__SimpleTable.hwpx` | ok | chrome-headless-cli | 1 | 1 |
| 3 | `reader_writer__HeaderFooter.hwpx` | ok | chrome-headless-cli | 1 | 1 |
| 4 | `reader_writer__MultiColumn.hwpx` | ok | chrome-headless-cli | 1 | 1 |
| 5 | `reader_writer__SimplePicture.hwpx` | ok | chrome-headless-cli | 1 | 1 |
| 6 | `reader_writer__PageFunctions.hwpx` | ok | chrome-headless-cli | 1 | 1 |
| 7 | `reader_writer__ChangeTrack.hwpx` | ok | chrome-headless-cli | 1 | 1 |
| 8 | `reader_writer__sample1.hwpx` | ok | chrome-headless-cli | 1 | 1 |
| 9 | `tool__textextractor__Table.hwpx` | ok | chrome-headless-cli | 1 | 1 |
| 10 | `error__20250523__프로젝트 계획서.hwpx` | ok | chrome-headless-cli | 1 | 1 |

All ten sample manifests included `hwpx.visual-review.v1` evidence and a
page-001 PNG path. This preview is a cheap layout eye; final acceptance for a
submission document still requires Hancom Office or human viewer review.
