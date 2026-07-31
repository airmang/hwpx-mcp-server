from __future__ import annotations

import zipfile

import pytest

from hwpx_automation import server
from hwpx_automation.hwpx_ops import HwpxOperationError


def _scripts(path: str) -> list[str]:
    import re

    with zipfile.ZipFile(path) as package:
        section = package.read("Contents/section0.xml").decode("utf-8")
    return re.findall(r"<hp:script>(.*?)</hp:script>", section, re.S)


def test_add_equation_from_latex_end_to_end(tmp_path) -> None:
    target = str(tmp_path / "eq.hwpx")
    server.create_document(target)
    created = server.add_equation(
        target, latex=r"x = \frac{-b \pm \sqrt{b^2 - 4ac}}{2a}"
    )
    assert created["ok"] is True
    equation = created["equation"]
    assert equation["script"] == "x = {- b +- sqrt {b ^{2} - 4 ac}} over {2 a}"
    assert equation["readerLatex"] == (
        "x = \\frac{- b \\pm \\sqrt{b^{2} - 4 ac}}{2 a}"
    )
    assert _scripts(target) == [equation["script"]]


def test_add_equation_raw_script(tmp_path) -> None:
    target = str(tmp_path / "raw.hwpx")
    server.create_document(target)
    script = "int _{0} ^{1} x^2 dx = {1} over {3}"
    created = server.add_equation(target, script=script)
    assert created["ok"] is True
    assert created["equation"]["script"] == script
    assert _scripts(target) == [script]


def test_add_equation_in_table_cell(tmp_path) -> None:
    target = str(tmp_path / "cell.hwpx")
    server.create_document(target)
    server.add_table(target, 2, 2)
    created = server.add_equation(
        target, latex=r"D = b^2 - 4ac", table_index=0, row=0, col=0
    )
    assert created["ok"] is True
    assert _scripts(target) == ["D = b ^{2} - 4 ac"]


def test_add_equation_unsupported_latex_typed_refusal(tmp_path) -> None:
    target = str(tmp_path / "bad.hwpx")
    server.create_document(target)
    with pytest.raises(HwpxOperationError) as excinfo:
        server.add_equation(target, latex=r"\mathbb{R}")
    assert getattr(excinfo.value, "code", None) == "EQUATION_LATEX_UNSUPPORTED"
    assert _scripts(target) == []


def test_add_equation_input_and_target_validation(tmp_path) -> None:
    target = str(tmp_path / "val.hwpx")
    server.create_document(target)
    with pytest.raises(HwpxOperationError) as excinfo:
        server.add_equation(target)
    assert getattr(excinfo.value, "code", None) == "EQUATION_INPUT_INVALID"
    with pytest.raises(HwpxOperationError) as excinfo:
        server.add_equation(target, latex="x", script="x")
    assert getattr(excinfo.value, "code", None) == "EQUATION_INPUT_INVALID"
    with pytest.raises(HwpxOperationError) as excinfo:
        server.add_equation(target, latex="x", table_index=0, row=0)
    assert getattr(excinfo.value, "code", None) == "EQUATION_TARGET_INVALID"
    with pytest.raises(HwpxOperationError) as excinfo:
        server.add_equation(target, latex="x", table_index=5, row=0, col=0)
    assert getattr(excinfo.value, "code", None) == "TABLE_INDEX_OUT_OF_RANGE"
    with pytest.raises(HwpxOperationError) as excinfo:
        server.add_equation(target, latex="x", paragraph_index=99)
    assert getattr(excinfo.value, "code", None) == "PARAGRAPH_INDEX_OUT_OF_RANGE"


def test_add_equation_dry_run_writes_nothing(tmp_path) -> None:
    target = tmp_path / "dry.hwpx"
    server.create_document(str(target))
    before = target.read_bytes()
    result = server.add_equation(str(target), latex=r"\frac{1}{2}", dry_run=True)
    assert result["ok"] is True
    assert target.read_bytes() == before
    assert _scripts(str(target)) == []
