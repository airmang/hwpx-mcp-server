from __future__ import annotations

import zipfile

import pytest

from hwpx_automation import server
from hwpx_automation.hwpx_ops import HwpxOperationError
from hwpx_automation.office.charting import (
    ChartSeries,
    UnsupportedChartError,
    build_chart_ml,
)


def _chart_parts(path: str) -> list[str]:
    with zipfile.ZipFile(path) as package:
        return sorted(n for n in package.namelist() if n.startswith("Chart/"))


SERIES = [
    {"name": "매출", "values": [10, 14, 12, 18]},
    {"name": "비용", "values": [8, 9, 11, 12]},
]
CATEGORIES = ["1분기", "2분기", "3분기", "4분기"]


def test_build_chart_ml_covers_the_three_verified_types() -> None:
    parsed = [ChartSeries("매출", (10.0, 14.0)), ChartSeries("비용", (8.0, 9.0))]
    for chart_type, marker in (
        ("bar", "<c:barChart>"),
        ("line", "<c:lineChart>"),
    ):
        xml = build_chart_ml(chart_type, ["A", "B"], parsed, title="제목")
        assert marker in xml
        assert "<c:catAx>" in xml and "<c:valAx>" in xml
        assert xml.count("<c:ser>") == 2
    pie = build_chart_ml("pie", ["A", "B"], parsed[:1])
    assert "<c:pieChart>" in pie
    assert "<c:catAx>" not in pie


def test_build_chart_ml_typed_refusals() -> None:
    one = [ChartSeries("s", (1.0, 2.0))]
    with pytest.raises(UnsupportedChartError):
        build_chart_ml("radar", ["A", "B"], one)
    with pytest.raises(UnsupportedChartError):
        build_chart_ml("bar", [], one)
    with pytest.raises(UnsupportedChartError):
        build_chart_ml("bar", ["A", "B"], [])
    with pytest.raises(UnsupportedChartError):
        build_chart_ml("pie", ["A", "B"], [ChartSeries("a", (1.0, 2.0)), ChartSeries("b", (3.0, 4.0))])
    with pytest.raises(UnsupportedChartError):
        build_chart_ml("bar", ["A", "B"], [ChartSeries("s", (1.0,))])
    with pytest.raises(UnsupportedChartError):
        build_chart_ml("bar", ["A", "B"], [ChartSeries("  ", (1.0, 2.0))])


def test_add_chart_end_to_end(tmp_path) -> None:
    target = str(tmp_path / "chart.hwpx")
    server.create_document(target)
    created = server.add_chart(
        target,
        chart_type="bar",
        categories=CATEGORIES,
        series=SERIES,
        title="분기 실적",
    )
    assert created["ok"] is True
    chart = created["chart"]
    assert chart["chartType"] == "bar"
    assert chart["chartIDRef"] == "Chart/chart1.xml"
    assert chart["seriesCount"] == 2
    assert _chart_parts(target) == ["Chart/chart1.xml"]
    with zipfile.ZipFile(target) as package:
        chart_ml = package.read("Chart/chart1.xml").decode("utf-8")
    assert "<c:barChart>" in chart_ml and "분기 실적" in chart_ml


def test_add_chart_in_table_cell_and_sequential_parts(tmp_path) -> None:
    target = str(tmp_path / "cell.hwpx")
    server.create_document(target)
    server.add_table(target, 2, 2)
    first = server.add_chart(
        target, chart_type="pie", categories=["A", "B"],
        series=[{"name": "비중", "values": [60, 40]}],
        table_index=0, row=0, col=0,
    )
    second = server.add_chart(
        target, chart_type="line", categories=CATEGORIES, series=SERIES,
    )
    assert first["chart"]["chartIDRef"] == "Chart/chart1.xml"
    assert second["chart"]["chartIDRef"] == "Chart/chart2.xml"
    assert _chart_parts(target) == ["Chart/chart1.xml", "Chart/chart2.xml"]


def test_add_chart_typed_refusals(tmp_path) -> None:
    target = str(tmp_path / "bad.hwpx")
    server.create_document(target)
    with pytest.raises(HwpxOperationError) as excinfo:
        server.add_chart(target, chart_type="radar", categories=["A"], series=[{"name": "s", "values": [1]}])
    assert getattr(excinfo.value, "code", None) == "CHART_UNSUPPORTED"
    with pytest.raises(HwpxOperationError) as excinfo:
        server.add_chart(target, chart_type="bar", categories=["A"], series=[{"name": "s", "values": ["x"]}])
    assert getattr(excinfo.value, "code", None) == "CHART_INPUT_INVALID"
    with pytest.raises(HwpxOperationError) as excinfo:
        server.add_chart(target, chart_type="bar", categories=["A"], series=[{"name": "s", "values": [1]}], table_index=0, row=0)
    assert getattr(excinfo.value, "code", None) == "CHART_TARGET_INVALID"
    assert _chart_parts(target) == []


def test_add_chart_dry_run_writes_nothing(tmp_path) -> None:
    target = tmp_path / "dry.hwpx"
    server.create_document(str(target))
    before = target.read_bytes()
    result = server.add_chart(
        str(target), chart_type="pie", categories=["A", "B"],
        series=[{"name": "비중", "values": [1, 2]}], dry_run=True,
    )
    assert result["ok"] is True
    assert target.read_bytes() == before
