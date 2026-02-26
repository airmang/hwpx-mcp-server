from __future__ import annotations

import base64
from pathlib import Path

import pytest

import hwpx_mcp_server.server as server


def _sample_hwpx_base64(tmp_path: Path) -> str:
    target = tmp_path / "read_export_sample.hwpx"
    server.create_document(str(target))
    server.add_heading(str(target), "Project Report", level=1)
    server.add_paragraph(str(target), "Overview paragraph.")
    server.add_heading(str(target), "Details", level=2)
    server.add_paragraph(str(target), "Figure 1 Example caption")
    server.add_table(str(target), 2, 2, [["Name", "Value"], ["A", "1"]])
    raw = target.read_bytes()
    return base64.b64encode(raw).decode("ascii")


def test_hwpx_to_markdown_from_base64(tmp_path: Path) -> None:
    payload = _sample_hwpx_base64(tmp_path)

    result = server.hwpx_to_markdown(hwpx_base64=payload)

    assert "Project Report" in result["markdown"]
    assert "| Name | Value |" in result["markdown"]
    assert result["meta"]["source_type"] == "base64"
    assert result["meta"]["table_count"] == 1


def test_hwpx_to_markdown_chunks_paragraph_strategy(tmp_path: Path) -> None:
    payload = _sample_hwpx_base64(tmp_path)

    result = server.hwpx_to_markdown(
        hwpx_base64=payload,
        output="chunks",
        chunk_strategy="paragraph",
        max_chars_per_chunk=32,
    )

    chunks = result.get("chunks")
    assert isinstance(chunks, list)
    assert chunks
    assert result["meta"]["chunk_strategy"] == "paragraph"


def test_hwpx_to_html_returns_html_and_meta(tmp_path: Path) -> None:
    payload = _sample_hwpx_base64(tmp_path)

    result = server.hwpx_to_html(hwpx_base64=payload)

    assert "<h1>Project Report</h1>" in result["html"]
    assert "<table>" in result["html"]
    assert result["meta"]["image_policy"] == "omitted"


def test_hwpx_extract_json_contains_toc_sections_tables_and_figures(tmp_path: Path) -> None:
    payload = _sample_hwpx_base64(tmp_path)

    result = server.hwpx_extract_json(hwpx_base64=payload, output="chunks")
    doc = result["doc"]

    assert doc["toc"]
    assert doc["sections"]
    assert doc["tables"]
    assert doc["figures"]
    assert result["chunks"]


def test_hwpx_conversion_input_validation(tmp_path: Path) -> None:
    payload = _sample_hwpx_base64(tmp_path)

    with pytest.raises(ValueError, match="exactly one"):
        server.hwpx_to_markdown()
    with pytest.raises(ValueError, match="invalid hwpx_base64"):
        server.hwpx_to_markdown(hwpx_base64="not-base64")
    with pytest.raises(ValueError, match="https"):
        server.hwpx_to_markdown(url="http://example.com/sample.hwpx")
    with pytest.raises(ValueError, match="output must be"):
        server.hwpx_to_markdown(hwpx_base64=payload, output="invalid-mode")


def test_main_defaults_to_stdio_transport(monkeypatch: pytest.MonkeyPatch) -> None:
    called: dict[str, str] = {}

    def _fake_run(*, transport: str) -> None:
        called["transport"] = transport

    monkeypatch.setattr(server.mcp, "run", _fake_run)

    server.main([])

    assert called["transport"] == "stdio"


def test_main_supports_streamable_http_transport(monkeypatch: pytest.MonkeyPatch) -> None:
    import uvicorn

    sentinel_app = object()
    captured: dict[str, object] = {}

    monkeypatch.setattr(server.mcp, "streamable_http_app", lambda: sentinel_app)

    def _fake_uvicorn_run(app, *, host: str, port: int, log_level: str) -> None:
        captured["app"] = app
        captured["host"] = host
        captured["port"] = port
        captured["log_level"] = log_level

    monkeypatch.setattr(uvicorn, "run", _fake_uvicorn_run)

    server.main(["--transport", "streamable-http", "--host", "127.0.0.1", "--port", "9911"])

    assert captured["app"] is sentinel_app
    assert captured["host"] == "127.0.0.1"
    assert captured["port"] == 9911
