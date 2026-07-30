from __future__ import annotations

from pathlib import Path

import pytest

from hwpx_automation import server
from hwpx_automation.hwpx_ops import HwpxOperationError


def test_add_form_field_end_to_end(tmp_path) -> None:
    target = str(tmp_path / "form.hwpx")
    server.create_document(target)
    created = server.add_form_field(
        target, name="성명", prompt="이름 입력", memo="도움말"
    )
    assert created["ok"] is True
    field = created["field"]
    assert field["name"] == "성명"
    assert field["prompt"] == "이름 입력"
    assert field["memo"] == "도움말"
    assert field["is_placeholder"] is True

    listing = server.list_form_fields(target)
    assert [f["name"] for f in listing["fields"]] == ["성명"]

    filled = server.fill_form_field(target, value="홍길동", name="성명")
    assert filled["field"]["current_value"] == "홍길동"
    assert filled["field"]["dirty"] == "1"


def test_add_form_field_in_table_cell(tmp_path) -> None:
    target = str(tmp_path / "cell.hwpx")
    server.create_document(target)
    server.add_table(target, 2, 2)
    created = server.add_form_field(
        target, name="셀필드", prompt="셀 입력", table_index=0, row=0, col=0
    )
    assert created["ok"] is True
    assert created["field"]["name"] == "셀필드"
    filled = server.fill_form_field(target, value="값1", name="셀필드")
    assert filled["field"]["current_value"] == "값1"


def test_add_form_field_target_validation(tmp_path) -> None:
    target = str(tmp_path / "bad.hwpx")
    server.create_document(target)
    with pytest.raises(HwpxOperationError) as excinfo:
        server.add_form_field(target, name="f", table_index=0, row=0)
    assert getattr(excinfo.value, 'code', None) == "FORM_FIELD_TARGET_INVALID"
    with pytest.raises(HwpxOperationError) as excinfo:
        server.add_form_field(target, name="f", table_index=5, row=0, col=0)
    assert getattr(excinfo.value, 'code', None) == "TABLE_INDEX_OUT_OF_RANGE"
    with pytest.raises(HwpxOperationError) as excinfo:
        server.add_form_field(target, name="f", paragraph_index=99)
    assert getattr(excinfo.value, 'code', None) == "PARAGRAPH_INDEX_OUT_OF_RANGE"


def test_add_form_field_dry_run_writes_nothing(tmp_path) -> None:
    target = tmp_path / "dry.hwpx"
    server.create_document(str(target))
    before = target.read_bytes()
    result = server.add_form_field(str(target), name="임시", prompt="안내", dry_run=True)
    assert result["ok"] is True
    assert target.read_bytes() == before
    assert server.list_form_fields(str(target))["fields"] == []
