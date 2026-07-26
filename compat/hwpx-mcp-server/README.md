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

기존 `hwpx-mcp-server` 명령은 6.x 호환 alias로 유지됩니다. 정식 MCP 명령은
`hwpx-automation-mcp`, MCP `serverInfo.name`은 `python-hwpx-automation`입니다.
호스트 설정의 `hwpx` key는 protocol identity가 아니라 각 호스트의 local alias입니다.

`import hwpx_mcp_server`와 공개 deep import는 6.x 동안 동작하며
`DeprecationWarning`을 냅니다. 새 이름은 `hwpx_automation`입니다. 제거는 7.0
이전에는 하지 않으며 최소 90일 공개 관찰과 별도 오너 승인이 필요합니다.

`importlib.resources`의 package data는 복제하지 않습니다. 리소스 소비자는
`importlib.resources.files("hwpx_automation")`을 사용해야 하며, 호환 셸에는
shim 외의 구현·데이터 사본이 없습니다.
