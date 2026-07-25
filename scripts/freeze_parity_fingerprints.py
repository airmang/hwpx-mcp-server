#!/usr/bin/env python3
"""Freeze structural fingerprints of core modules slated for deletion.

``python-hwpx`` is being reduced to a library: ``hwpx.agent``,
``hwpx.authoring``/``builder``/``design``/``presets``, ``hwpx.exam``,
``hwpx.guidance_scan``, and the application half of ``hwpx.visual`` are
scheduled for physical deletion from core once the MCP server is the
canonical owner of that runtime. Five MCP tests assert "the MCP copy matches
core's public shape" by importing both sides live; once core's side is
deleted those imports break and the assertions have no subject.

This script captures each of those core modules' public shape — via
``tests/parity_fingerprint.fingerprint()`` — into a checked-in JSON file
*while core still has them*, so the tests can compare the live MCP module's
fingerprint against a frozen record instead of a live core import.

Usage::

    python scripts/freeze_parity_fingerprints.py
    python scripts/freeze_parity_fingerprints.py --check
    PYTHON_HWPX_REPO=/path/to/python-hwpx python scripts/freeze_parity_fingerprints.py

Core is resolved the same way ``tests/conftest.py`` resolves it for the test
suite: an explicit ``PYTHON_HWPX_REPO`` pin, else the sibling worktree
matching this repo's name (``hwpx-mcp-server-X`` -> ``python-hwpx-X``), else
whatever ``hwpx`` is importable as. Never a bare unpinned ``../python-hwpx``.
"""

from __future__ import annotations

import argparse
import dataclasses
import importlib
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[1]
TESTS = ROOT / "tests"
OUTPUT_DIR = TESTS / "parity_fingerprints"
SCHEMA_VERSION = "hwpx.parity-fingerprint/v1"

if str(TESTS) not in sys.path:
    sys.path.insert(0, str(TESTS))

from parity_fingerprint import fingerprint

# Domain name -> dotted core module names that domain's parity test imports
# and needs frozen.
#
# hwpx.visual.oracle is NOT included in "exam": the exam parity test only
# used core's NullOracle/WordBox as convenience fixture inputs (never
# compared hwpx.visual.oracle's shape against anything), and the rewritten
# test builds those fixtures from hwpx.form_fit.wordbox (stays) and the MCP
# owner's own NullOracle instead. The "visual" domain freezes the full
# hwpx.visual.oracle surface for hwpx_mcp_server.office.rendering.oracle.
DOMAINS: dict[str, tuple[str, ...]] = {
    "agent": (
        "hwpx.agent",
        "hwpx.agent.blueprint",
        "hwpx.agent.cli",
    ),
    "authoring": (
        "hwpx.authoring",
        "hwpx.builder",
        "hwpx.design",
        "hwpx.presets",
        # The standing ownership ledger names these two explicitly as
        # mcp-migrate, and the MCP owner already carries both, so they go with
        # the rest of authoring rather than staying live because they happen to
        # sit under hwpx.tools.
        "hwpx.tools.advanced_generators",
        "hwpx.tools.style_profile",
    ),
    "exam": (
        "hwpx.exam",
        "hwpx.exam.compose",
        "hwpx.exam.ir",
        "hwpx.exam.measure",
        "hwpx.exam.parser",
        "hwpx.exam.profile",
    ),
    "form_fill": ("hwpx.guidance_scan",),
    "visual": (
        "hwpx.visual.oracle",
        "hwpx.visual.page_qa",
        "hwpx.visual.qa_metrics",
        "hwpx.visual.fixture_corpus",
        "hwpx.visual.hancom_worker",
    ),
}


def _matching_core_repo() -> Path:
    return ROOT.parent / ROOT.name.replace("hwpx-mcp-server", "python-hwpx", 1)


def resolve_core_repo() -> Path:
    """Resolve the python-hwpx checkout, matching tests/conftest.py's rule.

    Never falls back to a bare unpinned ``../python-hwpx``: that can silently
    freeze a different revision than the one this repo was branched from.
    """

    explicit = os.environ.get("PYTHON_HWPX_REPO")
    if explicit:
        return Path(explicit).expanduser().resolve()
    matching = _matching_core_repo()
    if matching.is_dir():
        return matching
    spec = importlib.util.find_spec("hwpx")
    if spec is None or spec.origin is None:
        raise RuntimeError(
            "python-hwpx is not installed; set PYTHON_HWPX_REPO to an explicit checkout"
        )
    package_dir = Path(spec.origin).resolve().parent
    if package_dir.parent.name == "src":
        return package_dir.parent.parent
    return package_dir.parent


def core_git_sha(core_repo: Path) -> str:
    result = subprocess.run(
        ["git", "-C", str(core_repo), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def _import_core_module(dotted_name: str) -> ModuleType:
    module = importlib.import_module(dotted_name)
    origin = getattr(module, "__file__", None) or ""
    if "hwpx_mcp_server" in origin:
        raise RuntimeError(
            f"{dotted_name} resolved to {origin!r}, not a core python-hwpx module; "
            "check sys.path ordering"
        )
    return module


def render_domain(domain: str, modules: tuple[str, ...], *, frozen_from: str) -> str:
    payload = {
        "schemaVersion": SCHEMA_VERSION,
        "frozenFrom": frozen_from,
        "modules": {
            name: fingerprint(_import_core_module(name)) for name in modules
        },
    }
    return json.dumps(payload, sort_keys=True, ensure_ascii=False, indent=2) + "\n"


# --- Golden call outputs -----------------------------------------------
#
# fingerprint() only captures shape (names/signatures/dataclass fields), not
# behaviour. A handful of parity assertions compare the *value* a
# deterministic, argument-free-or-fixed core call returns against the MCP
# owner's call — schema/catalog generators, a markdown parser, small pure
# geometry helpers. Values were confirmed identical between core and MCP and
# stable across repeat calls before being captured here (see the task
# report); this is snapshot/golden testing, not structural fingerprinting,
# so it lives in a sibling ``<domain>.golden.json`` with its own schema
# rather than inside the fingerprint payload.
GOLDEN_SCHEMA_VERSION = "hwpx.parity-golden/v1"


def _run_cli(main, args: list[str]) -> dict[str, object]:
    import io

    stdout, stderr = io.StringIO(), io.StringIO()
    code = main(list(args), stdin=io.StringIO(""), stdout=stdout, stderr=stderr)
    return {"exitCode": code, "stdout": stdout.getvalue(), "stderr": stderr.getvalue()}


def _build_agent_golden() -> dict[str, object]:
    agent = _import_core_module("hwpx.agent")
    blueprint = _import_core_module("hwpx.agent.blueprint")
    cli = _import_core_module("hwpx.agent.cli")

    command = {
        "commandId": "set-title",
        "op": "set",
        "path": "/section[1]/paragraph[1]",
        "properties": {"text": "동결"},
    }
    error_kwargs = {
        "code": "invalid_syntax",
        "message": "frozen",
        "target": "batch",
        "recoverability": "terminal",
        "suggestion": "retry",
        "valid_values": ("a", "b"),
    }
    try:
        agent.validate_agent_batch({})
        batch_error = None
    except agent.AgentContractError as exc:
        batch_error = {"code": exc.code, "target": exc.target, "message": str(exc)}

    cli_cases = [
        ["--version"],
        ["--help"],
        ["help", "--json"],
        ["help", "blueprint", "--json"],
        ["view", "--help"],
        ["unknown-command"],
    ]

    return {
        "agentContractManifest": agent.agent_contract_manifest(),
        "agentCatalog": agent.agent_catalog(),
        "agentJsonSchemas": agent.agent_json_schemas(),
        "mixedFormJsonSchemas": agent.mixed_form_json_schemas(),
        "blueprintCatalog": blueprint.blueprint_catalog(),
        "blueprintJsonSchemas": blueprint.blueprint_json_schemas(),
        "blueprintLimits": blueprint.blueprint_limits(),
        "validateAgentCommand": agent.validate_agent_command(command),
        "agentErrorToDict": agent.AgentError(**error_kwargs).to_dict(),
        "validateAgentBatchEmptyError": batch_error,
        "cliParserProg": cli.build_parser().prog,
        "cli": {" ".join(args): _run_cli(cli.main, args) for args in cli_cases},
    }


_STYLE_PROFILE = {
    "schemaVersion": "hwpx.style-profile.v1",
    "page": {
        "orientation": "LANDSCAPE",
        "widthMm": 297,
        "heightMm": 210,
        "marginsMm": {"left": 20, "right": 20, "top": 15, "bottom": 15},
    },
    "body": {"font": "함초롬바탕", "sizePt": 11},
}


def _authoring_plan() -> dict[str, object]:
    return {
        "schemaVersion": "hwpx.document_plan.v1",
        "title": "정본 parity",
        "blocks": [
            {"type": "heading", "level": 1, "text": "개요"},
            {"type": "paragraph", "text": "본문"},
            {
                "type": "table",
                "columns": [
                    {"key": "item", "label": "항목"},
                    {"key": "value", "label": "값"},
                ],
                "rows": [{"item": "A", "value": "1"}],
            },
        ],
    }


def _build_authoring_golden() -> dict[str, object]:
    import tempfile
    from pathlib import Path as _Path

    authoring = _import_core_module("hwpx.authoring")
    builder = _import_core_module("hwpx.builder")
    design = _import_core_module("hwpx.design")
    presets = _import_core_module("hwpx.presets")
    package_validator = _import_core_module("hwpx.tools.package_validator")

    plan = _authoring_plan()
    invalid_plan = {"schemaVersion": "hwpx.document_plan.v1", "blocks": [{}]}

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = _Path(tmp)

        document = authoring.create_document_from_plan(plan)
        try:
            plan_text = document.export_text()
            plan_path = tmp_path / "plan.hwpx"
            document.save_to_path(plan_path)
        finally:
            document.close()
        plan_safety = package_validator.validate_editor_open_safety(plan_path)

        lowered = builder.Document(
            sections=[
                builder.Section(
                    children=[
                        builder.Paragraph(text="{{ commas(1234567) }}"),
                        builder.Table(header=["항목", "값"], rows=[["A", "1"]]),
                    ]
                )
            ]
        ).lower()
        try:
            builder_text = lowered.export_text()
            builder_path = tmp_path / "builder.hwpx"
            lowered.save_to_path(builder_path)
        finally:
            lowered.close()
        builder_safety = package_validator.validate_editor_open_safety(builder_path)

    return {
        "imageGrid": _import_core_module("hwpx.tools.advanced_generators").build_image_grid(
            ["a.png", "b.png"], columns=2, image_width_mm=50
        ),
        "meetingNameplates": _import_core_module(
            "hwpx.tools.advanced_generators"
        ).build_meeting_nameplates(["가", "나"], columns=2),
        "organizationChart": _import_core_module(
            "hwpx.tools.advanced_generators"
        ).build_organization_chart({"name": "대표", "children": [{"name": "팀"}]}),
        "styleProfileApplied": _import_core_module(
            "hwpx.tools.style_profile"
        ).apply_style_profile_to_plan(_authoring_plan(), _STYLE_PROFILE),
        "documentPlanSchema": authoring.get_document_plan_schema(),
        "normalizedPlan": authoring.normalize_document_plan(plan).to_dict(),
        "validPlanReport": authoring.validate_document_plan(plan).to_dict(),
        "invalidPlanReport": authoring.validate_document_plan(invalid_plan).to_dict(),
        "normalizedProposalSpec": dataclasses.asdict(
            presets.normalize_proposal_spec(
                {"title": "제안", "sections": [{"title": "배경"}]}
            )
        ),
        "availableProfiles": design.available_profiles(),
        "planDocument": {
            "exportText": plan_text,
            "openSafetyOk": plan_safety.ok,
            "openSafetySummary": plan_safety.summary,
        },
        "builderDocument": {
            "exportText": builder_text,
            "openSafetyOk": builder_safety.ok,
            "openSafetySummary": builder_safety.summary,
        },
    }


_EXAM_GOOD_MARKDOWN = """# 중간고사

## 1. (3점)
다음 중 옳은 것은? [그림1]
① 가
② 나

## 2∼3. 세트
공통 지문
### 2.
둘째 발문
① 다
### 3. (2점)
셋째 발문
① 라
"""
_EXAM_BAD_MARKDOWN = "본문이 문항 헤더 없이 먼저 나온다.\n## 1.\n발문\n"


def _build_exam_golden() -> dict[str, object]:
    import hashlib
    import tempfile
    import zipfile
    from pathlib import Path as _Path
    from random import Random
    from uuid import UUID

    oxml_document = _import_core_module("hwpx.oxml.document")
    parser = _import_core_module("hwpx.exam.parser")
    measure = _import_core_module("hwpx.exam.measure")
    profile = _import_core_module("hwpx.exam.profile")
    compose = _import_core_module("hwpx.exam.compose")
    document_module = _import_core_module("hwpx.document")
    wordbox_module = _import_core_module("hwpx.form_fit.wordbox")
    oracle_module = _import_core_module("hwpx.visual.oracle")

    # The exam corpus is read from the core repository rather than copied:
    # duplicating a vendored real-world school document into a second public
    # repository is a redistribution decision, and the core checkout is already
    # resolved here without importing the package that is being removed.
    fixtures_dir = resolve_core_repo() / "tests" / "fixtures" / "exam"

    doc = parser.parse_exam_markdown(_EXAM_GOOD_MARKDOWN)
    try:
        parser.parse_exam_markdown(_EXAM_BAD_MARKDOWN)
        bad_markdown_error = None
    except parser.ExamParseError as exc:
        bad_markdown_error = {
            "str": str(exc),
            "lineNo": exc.line_no,
            "text": exc.text,
            "reason": exc.reason,
        }

    def _glyph(text: str, x: float, y: float, *, line: int):
        return wordbox_module.WordBox(
            x0=x, y0=y, x1=x + 8, y1=y + 12, text=text, page=0, block=0, line=line, word_no=0,
        )

    glyphs = [
        _glyph("1", 10, 10, line=0),
        _glyph(".", 18, 10, line=0),
        _glyph("①", 10, 30, line=1),
        _glyph("가", 18, 30, line=1),
        _glyph("2", 330, 10, line=2),
        _glyph(".", 338, 10, line=2),
    ]
    bounds = measure.column_x_bounds(glyphs)
    blocks = [
        [block.id, [glyph.text for glyph in block.glyphs]]
        for block in measure.group_question_blocks(glyphs)
    ]

    profile_value = profile.profile_form(
        document_module.HwpxDocument.open(fixtures_dir / "A_form.hwpx")
    )

    def _deterministic_uuid4(seed: int):
        rng = Random(seed)
        return lambda: UUID(int=rng.getrandbits(128))

    with tempfile.TemporaryDirectory() as tmp:
        out_path = _Path(tmp) / "composed.hwpx"
        oxml_document.uuid4 = _deterministic_uuid4(103)
        markdown_text = (fixtures_dir / "sample_exam.md").read_text(encoding="utf-8")
        result = compose.compose_exam_into_form(
            str(fixtures_dir / "A_form.hwpx"),
            markdown_text,
            str(out_path),
            oracle=oracle_module.NullOracle(),
        )
        result_projection = dataclasses.asdict(result)
        result_projection["out_path"] = "<output>"
        with zipfile.ZipFile(out_path) as archive:
            names = sorted(archive.namelist())
            entry_hashes = {
                name: hashlib.sha256(archive.read(name)).hexdigest() for name in names
            }

    return {
        "parseGoodMarkdown": {
            "docAsDict": dataclasses.asdict(doc),
            "questionNumbers": [q.number for q in doc.iter_questions()],
        },
        "parseBadMarkdownError": bad_markdown_error,
        "columnXBounds": list(bounds),
        "groupQuestionBlocks": blocks,
        "profileFormAsDict": dataclasses.asdict(profile_value),
        "composeResult": {
            "resultProjection": result_projection,
            "zipEntryNames": names,
            "zipEntrySha256": entry_hashes,
        },
    }


def _build_visual_golden() -> dict[str, object]:
    import hashlib
    import json as _json
    import os
    import tempfile
    from pathlib import Path as _Path

    from PIL import Image, ImageDraw

    oracle = _import_core_module("hwpx.visual.oracle")
    fixture_corpus = _import_core_module("hwpx.visual.fixture_corpus")
    page_qa = _import_core_module("hwpx.visual.page_qa")
    qa_metrics = _import_core_module("hwpx.visual.qa_metrics")
    worker = _import_core_module("hwpx.visual.hancom_worker")

    previous_env = os.environ.get("HWPX_ORACLE_STRUCTURAL_ONLY")
    os.environ["HWPX_ORACLE_STRUCTURAL_ONLY"] = "1"
    try:
        backend = oracle.resolve_oracle()
        backend_type = type(backend).__name__
        report = oracle.visual_check("before.hwpx", "after.hwpx", oracle=backend)
        report_dict = report.to_dict()
    finally:
        if previous_env is None:
            del os.environ["HWPX_ORACLE_STRUCTURAL_ONLY"]
        else:
            os.environ["HWPX_ORACLE_STRUCTURAL_ONLY"] = previous_env

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = _Path(tmp)
        page = tmp_path / "page.png"
        image = Image.new("RGB", (400, 600), "white")
        draw = ImageDraw.Draw(image)
        for offset in range(150):
            draw.point(
                (80 + (offset * 17) % 240, 80 + (offset * 29) % 440), fill="black"
            )
        image.save(page)
        manifest = {
            "schema": "hwpx.visual-fixture-manifest/v1",
            "taxonomyVersion": "hwpx-visual-defects/1.0",
            "assurance": "fixture",
            "cases": [
                {
                    "id": "clean-parity",
                    "classification": "clean",
                    "pages": [
                        {
                            "page": 0,
                            "path": page.name,
                            "sha256": hashlib.sha256(page.read_bytes()).hexdigest(),
                        }
                    ],
                    "annotations": [],
                    "provenance": {"kind": "synthetic-parity"},
                }
            ],
        }
        manifest_path = tmp_path / "manifest.json"
        manifest_path.write_text(_json.dumps(manifest), encoding="utf-8")

        corpus = fixture_corpus.load_fixture_manifest(manifest_path)
        receipt = corpus.receipt(corpus.cases[0])
        qa_dict = page_qa.inspect_fixture_case(corpus.cases[0]).to_dict()
        metrics = qa_metrics.measure_fixture_corpus(corpus)

        def fake_rasterize(_pdf, destination, _dpi):
            rasterized = destination / "page-0001.png"
            rasterized.write_bytes(b"PNG-PARITY")
            return [rasterized]

        worker.SerializedHancomWorker._rasterize = staticmethod(fake_rasterize)
        source = tmp_path / "input.hwpx"
        source.write_bytes(b"worker-parity")
        digest = worker.sha256_file(source)
        instance = worker.SerializedHancomWorker(
            tmp_path / "worker",
            session_factory=worker.DeterministicFakeSession,
            worker_version="parity",
        )
        try:
            success = instance.render(worker.WorkerJob("success", source, digest))
            failure = instance.render(
                worker.WorkerJob("failure", source, "sha256:wrong")
            )
            success_json = _json.loads(success.to_json())
            failure_json = _json.loads(failure.to_json())
        finally:
            instance.close()

    return {
        "structuralOnly": {
            "backendType": backend_type,
            "reportToDict": report_dict,
        },
        "fixtureQa": {
            "receipt": receipt,
            "qaToDict": qa_dict,
            "metrics": metrics,
        },
        "worker": {
            "successToJson": success_json,
            "failureToJson": failure_json,
        },
    }


GOLDEN_BUILDERS: dict[str, object] = {
    "agent": _build_agent_golden,
    "authoring": _build_authoring_golden,
    "exam": _build_exam_golden,
    "visual": _build_visual_golden,
}


def render_golden(domain: str, *, frozen_from: str) -> str | None:
    builder = GOLDEN_BUILDERS.get(domain)
    if builder is None:
        return None
    payload = {
        "schemaVersion": GOLDEN_SCHEMA_VERSION,
        "frozenFrom": frozen_from,
        "calls": builder(),
    }
    return json.dumps(payload, sort_keys=True, ensure_ascii=False, indent=2) + "\n"


def _sync(path: Path, content: str, *, check: bool) -> bool:
    current = path.read_text(encoding="utf-8") if path.is_file() else None
    if current == content:
        print(f"ok: {path}")
        return True
    if check:
        print(f"drift: {path}", file=sys.stderr)
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    print(f"wrote: {path}")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="verify the checked-in JSON matches a fresh freeze instead of writing",
    )
    parser.add_argument(
        "--domain",
        action="append",
        choices=sorted(DOMAINS),
        help="freeze only this domain (repeatable); default: all domains",
    )
    args = parser.parse_args()

    core_repo = resolve_core_repo()
    core_src = core_repo / "src"
    if not core_src.is_dir():
        print(f"error: {core_src} does not look like a python-hwpx checkout", file=sys.stderr)
        return 1
    # Prepend so this worktree's core wins over any installed hwpx package.
    sys.path.insert(0, str(core_src))

    frozen_from = f"python-hwpx {core_git_sha(core_repo)}"
    print(f"freezing from: {frozen_from} ({core_repo})")

    domains = args.domain or sorted(DOMAINS)
    ok = True
    for domain in domains:
        content = render_domain(domain, DOMAINS[domain], frozen_from=frozen_from)
        if not _sync(OUTPUT_DIR / f"{domain}.json", content, check=args.check):
            ok = False
        golden = render_golden(domain, frozen_from=frozen_from)
        if golden is not None and not _sync(
            OUTPUT_DIR / f"{domain}.golden.json", golden, check=args.check
        ):
            ok = False
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
