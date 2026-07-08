# SPDX-License-Identifier: Apache-2.0
"""describe_capabilities — 작업군 능력 지도 + 커버리지 드리프트 가드.

핵심 계약: 등록된 모든 도구가 지도의 정확히 한 작업군에 매핑돼야 한다. 새 도구를
추가하고 이 지도에 안 넣으면 이 테스트가 실패해 드리프트를 잡는다(자기유지).
"""
from __future__ import annotations

from hwpx_mcp_server.capabilities import DOMAINS, build_capability_report, coverage_against


def _live_tools() -> set[str]:
    import hwpx_mcp_server.server as s

    # 서버가 쓰는 동기 헬퍼 사용(asyncio 이벤트루프 공유 상태에 취약하지 않게)
    return set(s._fastmcp_tool_names()) | set(s._legacy_tool_names())


def test_every_registered_tool_is_mapped():
    cov = coverage_against(_live_tools())
    assert cov["unmapped"] == [], f"지도에 없는 도구(드리프트): {cov['unmapped']}"


def test_no_phantom_tools_in_map():
    cov = coverage_against(_live_tools())
    assert cov["mappedNotRegistered"] == [], f"등록 안 된 지도 항목(오타?): {cov['mappedNotRegistered']}"


def test_each_tool_in_exactly_one_domain():
    seen: dict[str, str] = {}
    for d in DOMAINS:
        for t in d["tools"]:
            assert t not in seen, f"{t}가 {seen.get(t)}·{d['key']} 두 작업군에 중복"
            seen[t] = d["key"]


def test_report_shape_and_domain_filter():
    full = build_capability_report()
    assert full["domainCount"] == len(DOMAINS) >= 12
    assert all({"key", "title", "intent", "whenToUse", "tools"} <= set(dm) for dm in full["domains"])
    one = build_capability_report("form_fill")
    assert len(one["domains"]) == 1 and one["domains"][0]["key"] == "form_fill"
    assert "scan_form_guidance" in one["domains"][0]["tools"]


def test_mcp_tool_registered_and_callable():
    import hwpx_mcp_server.server as s

    assert "describe_capabilities" in set(s._fastmcp_tool_names())
    out = s.describe_capabilities()
    assert out["coverage"]["unmapped"] == [] and out["toolCount"] >= 140
