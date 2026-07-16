# SPDX-License-Identifier: Apache-2.0
"""describe_capabilities — 작업군 능력 지도 + 커버리지 드리프트 가드.

핵심 계약: 등록된 모든 도구가 지도의 정확히 한 작업군에 매핑돼야 한다. 새 도구를
추가하고 이 지도에 안 넣으면 이 테스트가 실패해 드리프트를 잡는다(자기유지).
"""
from __future__ import annotations

from hwpx_mcp_server.capabilities import DOMAINS, build_capability_report, coverage_against
from hwpx_mcp_server.tool_contract import contract_hash, expected_tool_names, skill_required_tool_names


def _live_tools() -> set[str]:
    import hwpx_mcp_server.server as s

    return set(s._fastmcp_tool_names())


def _active_advanced() -> bool:
    import hwpx_mcp_server.server as s

    return s._ACTIVE_ADVANCED


def test_every_registered_tool_is_mapped():
    cov = coverage_against(_live_tools(), advanced=_active_advanced())
    assert cov["ok"] is True, cov


def test_no_phantom_tools_in_map():
    cov = coverage_against(_live_tools(), advanced=_active_advanced())
    assert cov["missingExpected"] == []
    assert _live_tools() == expected_tool_names(advanced=_active_advanced())


def test_each_tool_in_exactly_one_domain():
    seen: dict[str, str] = {}
    for d in DOMAINS:
        for t in d["tools"]:
            assert t not in seen, f"{t}가 {seen.get(t)}·{d['key']} 두 작업군에 중복"
            seen[t] = d["key"]


def test_report_shape_and_domain_filter():
    full = build_capability_report()
    assert full["domainCount"] >= 12
    assert full["profile"] == "default"
    assert full["contractHash"] == contract_hash()
    assert all({"key", "title", "intent", "whenToUse", "tools"} <= set(dm) for dm in full["domains"])
    one = build_capability_report("form_fill")
    assert len(one["domains"]) == 1 and one["domains"][0]["key"] == "form_fill"
    assert "scan_form_guidance" in one["domains"][0]["tools"]


def test_mcp_tool_registered_and_callable():
    import hwpx_mcp_server.server as s

    assert "describe_capabilities" in set(s._fastmcp_tool_names())
    out = s.describe_capabilities()
    assert out["coverage"]["ok"] is True
    assert out["toolCount"] == len(expected_tool_names(advanced=_active_advanced()))
    active = expected_tool_names(advanced=_active_advanced())
    assert skill_required_tool_names() & active <= set(s._fastmcp_tool_names())
