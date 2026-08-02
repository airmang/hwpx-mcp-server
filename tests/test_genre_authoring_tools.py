# SPDX-License-Identifier: Apache-2.0
"""Characterization for the operating-plan genre authoring surface (6.5.0)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import hwpx_automation.server as server
from hwpx_automation.office.house_style.org_chart import (
    MAX_BOXES,
    compose_boxed_org_chart,
)

HP = "{http://www.hancom.co.kr/hwpml/2011/paragraph}"


@pytest.fixture()
def workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("HWPX_AUTOMATION_WORKSPACE_ROOTS", json.dumps([str(tmp_path)]))
    return tmp_path


def _tree() -> dict:
    return {
        "label": "운영단",
        "sublabel": "단장 1명",
        "children": [
            {"label": "기획팀", "children": [{"label": "기획"}, {"label": "예산"}]},
            {"label": "지원팀", "children": [{"label": "상담"}]},
        ],
    }


class TestComposeBoxedOrgChart:
    def test_grid_geometry_matches_leaf_layout(self) -> None:
        spec = compose_boxed_org_chart(_tree())
        # 3 leaves → 3*3-1 columns; depth 3 → 3*3-2 rows.
        assert (spec.rows, spec.cols) == (7, 8)
        assert len(spec.boxes) == 6
        # 커넥터는 스텁(부모1+자식N)과 레일로만 구성된다.
        assert spec.cell_sides

    def test_boxes_sit_on_box_rows_and_root_gets_accent(self) -> None:
        spec = compose_boxed_org_chart(_tree(), accent_color="#DCE6F1")
        assert all(box.row % 3 == 0 for box in spec.boxes)
        root = next(box for box in spec.boxes if box.row == 0)
        assert root.fill_color == "#DCE6F1"
        assert all(box.fill_color is None for box in spec.boxes if box.row != 0)

    def test_depth_and_box_limits_are_typed_refusals(self) -> None:
        deep = {"label": "1", "children": [{"label": "2", "children": [
            {"label": "3", "children": [{"label": "4", "children": [{"label": "5"}]}]}
        ]}]}
        with pytest.raises(ValueError):
            compose_boxed_org_chart(deep)
        wide = {
            "label": "root",
            "children": [
                {"label": f"n{i}", "children": [{"label": f"m{i}{j}"} for j in range(3)]}
                for i in range(20)
            ],
        }
        with pytest.raises(ValueError):
            compose_boxed_org_chart(wide)
        assert MAX_BOXES == 40

    def test_empty_label_is_refused(self) -> None:
        with pytest.raises(Exception):
            compose_boxed_org_chart({"label": "  "})


class TestAddBoxedOrgChartTool:
    def test_writes_chart_and_reports_summary(self, workspace: Path) -> None:
        target = workspace / "chart.hwpx"
        server.create_document(str(target))
        result = server.add_boxed_org_chart(str(target), _tree(), accent_color="#DCE6F1")
        assert result["orgChart"]["boxes"] == 6
        assert result["orgChart"]["rows"] == 7
        from hwpx import HwpxDocument

        doc = HwpxDocument.open(str(target))
        tables = [t for section in doc.sections for p in section.paragraphs for t in p.tables]
        assert tables, "chart table must persist"
        # 병합 박스 라벨 실재 확인
        joined = "".join(
            el.text or ""
            for section in doc.sections
            for el in section.element.iter(f"{HP}t")
        )
        assert "운영단" in joined and "지원팀" in joined

    def test_dry_run_does_not_persist(self, workspace: Path) -> None:
        target = workspace / "dry.hwpx"
        server.create_document(str(target))
        before = target.read_bytes()
        server.add_boxed_org_chart(str(target), _tree(), dry_run=True)
        assert target.read_bytes() == before


class TestGenreGrammarAndChip:
    def test_grammar_lookup_returns_typography_and_inheritance(self) -> None:
        result = server.get_genre_grammar("operating_plan")
        assert result["genre"] == "operating_plan"
        assert "typography" in result["grammar"]
        assert result["inheritedProfile"]["name"] == "report"
        assert result["orgChartTool"] == "add_boxed_org_chart"

    def test_unknown_genre_is_typed_refusal_with_catalog(self) -> None:
        with pytest.raises(ValueError) as exc:
            server.get_genre_grammar("novel")
        assert "operating_plan" in str(exc.value)

    def test_section_chip_box_lowers_to_generic_table_block(self) -> None:
        result = server.compose_section_chip("Ⅰ", "추진 배경", accent_color="#1F4E79")
        block = result["block"]
        assert block["type"] == "table"
        assert block["showHeader"] is False
        assert [column["key"] for column in block["columns"]] == ["number", "gap", "title"]
        assert block["rows"] == [{"number": "Ⅰ", "gap": "", "title": "추진 배경"}]
        assert result["styling"]["accentColor"] == "#1F4E79"

    def test_section_chip_inline_and_style_refusal(self) -> None:
        inline = server.compose_section_chip("1", "현황", style="inline")
        assert inline["block"]["type"] == "heading"
        with pytest.raises(ValueError):
            server.compose_section_chip("1", "현황", style="banner")
