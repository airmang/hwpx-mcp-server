from pydantic import TypeAdapter

from hwpx_mcp_server.core.locator import (
    DocumentLocator,
    locator_identifier,
    locator_path,
    normalize_locator_payload,
)
from hwpx_mcp_server.core.plan import PlanEditInput


LOCATOR_ADAPTER = TypeAdapter(DocumentLocator)


def _locator(payload: dict[str, object]) -> DocumentLocator:
    normalized = normalize_locator_payload(payload)
    return LOCATOR_ADAPTER.validate_python(normalized["document"])


def test_document_locator_accepts_legacy_path() -> None:
    locator = _locator({"path": "sample.hwpx"})
    assert locator_path(locator) == "sample.hwpx"
    assert locator_identifier(locator) == "sample.hwpx"


def test_document_locator_accepts_uri_variant() -> None:
    uri = "https://example.com/sample.hwpx"
    locator = _locator({"type": "uri", "uri": uri, "backend": "http"})
    assert locator_path(locator) == uri
    assert locator_identifier(locator) == uri


def test_document_locator_handle_supports_handle_id_flow() -> None:
    locator = _locator({"type": "handle", "handleId": "doc-123"})
    assert locator_path(locator) is None
    assert locator_identifier(locator) == "doc-123"


def test_plan_edit_input_surface_doc_id_for_handle() -> None:
    payload = PlanEditInput.model_validate(
        {
            "type": "handle",
            "handleId": "registered-doc",
            "operations": [
                {
                    "target": {"nodeId": "n_deadbeef"},
                    "match": "needle",
                    "replacement": "haystack",
                }
            ],
        }
    )
    assert payload.doc_id == "registered-doc"
    assert payload.to_hwpx_payload(require_path=False)["operations"]
