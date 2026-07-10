"""Task-oriented capability report derived from the FastMCP ToolSpec contract."""

from __future__ import annotations

from typing import Any

from .tool_contract import DOMAIN_SPECS, TOOL_SPECS, contract_hash, expected_tool_names


# Backward-compatible data shape for callers that imported DOMAINS directly.
DOMAINS: list[dict[str, Any]] = [
    {
        "key": domain.key,
        "title": domain.title,
        "intent": domain.intent,
        "whenToUse": domain.when_to_use,
        "tools": list(domain.tools),
    }
    for domain in DOMAIN_SPECS
]


def build_capability_report(
    domain: str | None = None,
    *,
    advanced: bool = False,
) -> dict[str, Any]:
    """Return only tools callable on the active FastMCP profile."""

    active = expected_tool_names(advanced=advanced)
    selected = DOMAIN_SPECS if domain is None else tuple(item for item in DOMAIN_SPECS if item.key == domain)
    out_domains: list[dict[str, Any]] = []
    for item in selected:
        tools = [name for name in item.tools if name in active]
        if not tools and domain is None:
            continue
        out_domains.append(
            {
                "key": item.key,
                "title": item.title,
                "intent": item.intent,
                "whenToUse": item.when_to_use,
                "toolCount": len(tools),
                "tools": tools,
            }
        )
    return {
        "domains": out_domains,
        "domainCount": len(out_domains),
        "profile": "advanced" if advanced else "default",
        "contractHash": contract_hash(),
        "note": (
            "실제 FastMCP 등록 표면의 작업군 지도. legacy inventory는 호출 가능 도구 수에 포함하지 않는다. "
            "양식 채움 상세 절차는 스킬 references/workflows-forms.md를 따른다."
        ),
    }


def coverage_against(tool_names: set[str], *, advanced: bool = False) -> dict[str, Any]:
    """Compare a live registry to the exact active ToolSpec set."""

    expected = expected_tool_names(advanced=advanced)
    missing = sorted(expected - tool_names)
    unexpected = sorted(tool_names - expected)
    return {
        "missingExpected": missing,
        "unexpectedRegistered": unexpected,
        # Compatibility keys retained for older health consumers/tests.
        "unmapped": unexpected,
        "mappedNotRegistered": missing,
        "ok": not missing and not unexpected,
    }


def tool_domain(name: str) -> str | None:
    for spec in TOOL_SPECS:
        if spec.name == name:
            return spec.domain
    return None


__all__ = ["DOMAINS", "build_capability_report", "coverage_against", "tool_domain"]
