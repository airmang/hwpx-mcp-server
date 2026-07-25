# hwpx-mcp-server — 호환 배포

이 이름은 6.0.0부터 **얇은 셸**입니다. 실제 코드는
[`python-hwpx-automation`](https://pypi.org/project/python-hwpx-automation/)에
있습니다.

응용 계층 5만 줄 중 MCP 전송은 4%뿐이라, 그 4%의 이름이 전체를 대표하고
있었습니다. 이름을 제자리로 옮기되 **기존 설치와 MCP 설정은 그대로 둡니다.**

```bash
pip install hwpx-mcp-server          # 계속 동작 — automation[mcp]를 끌어옵니다
pip install python-hwpx-automation   # 새 이름. MCP 없이 파이썬 API만
```

`hwpx-mcp-server` 명령과 MCP server id는 **바뀌지 않았습니다.**

`import hwpx_mcp_server`도 한 major 동안 동작하며 `DeprecationWarning`을 냅니다.
새 이름은 `hwpx_automation`입니다.
