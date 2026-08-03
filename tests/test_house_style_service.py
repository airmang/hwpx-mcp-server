# SPDX-License-Identifier: Apache-2.0
"""Relocated S-097 house-style data and composition service."""
from __future__ import annotations

import json
from importlib import resources

import pytest

from hwpx_automation.office import house_style


def test_packaged_bank_loads_as_typed_deterministic_data() -> None:
    first = house_style.load_bank()
    second = house_style.load_bank()
    assert first is second
    assert first.schema_version == house_style.BANK_SCHEMA_VERSION
    assert house_style.list_profiles() == sorted(first.profiles)
    assert {
        "default_document",
        "gongmun",
        "home_notice",
        "personal_modern",
        "report",
    } <= set(first.profiles)
    assert house_style.get_profile("report")["roles"]["body"]["font"] == "휴먼명조"


def test_packaged_genre_inherits_profile_roles() -> None:
    catalog = house_style.load_genres()
    assert catalog.schema_version == house_style.GENRES_SCHEMA_VERSION
    assert house_style.list_genres() == ["official_draft", "operating_plan", "simple_draft"]
    grammar = house_style.get_genre("operating_plan").grammar
    assert grammar["blockTypes"]["section_chip"]["required"] is True
    assert grammar["blockTypes"]["org_chart"]["required"] is False
    typography = house_style.genre_typography("operating_plan")
    assert typography["inherits"] == "report"
    assert typography["roles"]["body"]["font"] == "휴먼명조"
    assert "section_chip_number" in typography["roles"]


def test_schema_validation_fails_closed() -> None:
    raw = json.loads(
        resources.files(house_style)
        .joinpath("data", "bank.json")
        .read_text(encoding="utf-8")
    )
    raw["schemaVersion"] = "wrong"
    with pytest.raises(house_style.HouseStyleDataError, match="invalid house-style bank"):
        house_style.validate_bank(raw)

    with pytest.raises(house_style.HouseStyleDataError, match="invalid house-style genres"):
        house_style.validate_genres(
            {
                "schemaVersion": house_style.GENRES_SCHEMA_VERSION,
                "generated": "2026-07-23",
                "_usage": "fixture",
                "genres": {},
            }
        )


def test_font_resolution_is_data_only() -> None:
    assert house_style.resolve_font("휴먼명조") == "휴먼명조"
    assert house_style.resolve_font("휴먼명조", mode="portable") == "함초롬바탕"
    banned = {"classify", "detect_document_type", "route", "select_profile", "guess_genre"}
    assert not (banned & set(house_style.__all__))


def test_section_chip_composes_generic_blocks() -> None:
    boxed = house_style.compose_section_chip("Ⅰ", "근거", accent_color="#3366CC")
    assert boxed["type"] == "table"
    # 실제 document_plan v1 표 어휘(columns 객체 + rows 매핑, 헤더 억제)로 lower된다.
    assert boxed["showHeader"] is False
    assert [column["key"] for column in boxed["columns"]] == ["number", "gap", "title"]
    assert boxed["rows"] == [{"number": "Ⅰ", "gap": "", "title": "근거"}]

    inline = house_style.compose_section_chip("1", "추진 목적", style="inline")
    assert inline["type"] == "heading"
    assert inline["text"] == "1 추진 목적"


def test_section_chip_rejects_invalid_values() -> None:
    with pytest.raises(ValueError):
        house_style.compose_section_chip("", "제목")
    with pytest.raises(ValueError):
        house_style.compose_section_chip("Ⅰ", "")
    with pytest.raises(ValueError):
        house_style.SectionChipPlan(number="Ⅰ", title="제목", style="fancy")
