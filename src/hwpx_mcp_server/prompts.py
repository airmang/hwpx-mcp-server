"""MCP prompts/list, prompts/get에 노출할 프롬프트 정의."""

from __future__ import annotations

from dataclasses import dataclass
from string import Formatter
from typing import Mapping, Sequence

import mcp.types as types


@dataclass(frozen=True)
class PromptArgumentSpec:
    """프롬프트 인자 메타데이터."""

    name: str
    description: str
    required: bool = True
    schema: Mapping[str, object] | None = None

    def to_prompt_argument(self) -> types.PromptArgument:
        return types.PromptArgument(
            name=self.name,
            description=self.description,
            required=self.required,
        )


@dataclass(frozen=True)
class ToolBinding:
    """템플릿 변수와 도구 인자의 매핑 정의."""

    tool_name: str
    arguments_template: str


@dataclass(frozen=True)
class PromptTemplate:
    """버전 가능한 프롬프트 템플릿 정의."""

    prompt_id: str
    version: str
    title: str
    description: str
    template: str
    arguments: Sequence[PromptArgumentSpec]
    tool_bindings: Sequence[ToolBinding]
    example_input: Mapping[str, str]
    example_output: str

    @property
    def name(self) -> str:
        # 버전 호환성 관리를 위해 ID에 명시적으로 버전을 포함한다.
        return f"{self.prompt_id}@{self.version}"

    def to_prompt(self) -> types.Prompt:
        return types.Prompt(
            name=self.name,
            title=self.title,
            description=self._description_block(),
            arguments=[item.to_prompt_argument() for item in self.arguments],
        )

    def render(self, values: Mapping[str, str] | None) -> types.GetPromptResult:
        normalized = self._normalize_values(values)
        text = self.template.format(**normalized)
        return types.GetPromptResult(
            description=self._description_block(),
            messages=[
                types.PromptMessage(
                    role="user",
                    content=types.TextContent(type="text", text=text),
                )
            ],
        )

    def _description_block(self) -> str:
        schema_lines = [
            f"- `{arg.name}`: {arg.schema if arg.schema is not None else {'type': 'string'}}"
            for arg in self.arguments
        ]
        binding_lines = [
            f"- `{binding.tool_name}` ← {binding.arguments_template}"
            for binding in self.tool_bindings
        ]
        return "\n".join(
            [
                self.description,
                "",
                f"prompt_id: `{self.prompt_id}`",
                f"version: `{self.version}`",
                "",
                "인자 스키마:",
                *schema_lines,
                "",
                "도구 매핑(도구 이름/인자명 ↔ 템플릿 변수):",
                *binding_lines,
                "",
                f"예시 입력: {dict(self.example_input)}",
                f"예시 출력: {self.example_output}",
            ]
        )

    def _normalize_values(self, values: Mapping[str, str] | None) -> dict[str, str]:
        incoming = dict(values or {})
        normalized: dict[str, str] = {}
        for argument in self.arguments:
            raw = incoming.get(argument.name)
            if raw is None:
                if argument.required:
                    raise ValueError(f"프롬프트 인자 '{argument.name}'가 필요합니다.")
                normalized[argument.name] = ""
                continue
            normalized[argument.name] = str(raw)

        for _, field_name, _, _ in Formatter().parse(self.template):
            if not field_name:
                continue
            normalized.setdefault(field_name, "")

        return normalized


PROMPT_TEMPLATES: tuple[PromptTemplate, ...] = (
    PromptTemplate(
        prompt_id="summary",
        version="v1",
        title="문서 요약",
        description="HWPX 문서 전체 텍스트를 읽어 지정 길이로 요약합니다.",
        template=(
            "아래 순서로 HWPX 문서를 요약해 주세요.\n"
            "1) 도구 `open_info`를 호출해 문서 기본 정보(문단 수, 섹션 수)를 확인\n"
            "   - arguments: {{\"path\": \"{path}\"}}\n"
            "2) 도구 `text_extract_report`를 호출해 전체 텍스트를 읽기\n"
            "   - arguments: {{\"path\": \"{path}\", \"mode\": \"plain\"}}\n"
            "3) 추출 텍스트를 {summaryStyle} 스타일로 {maxSentences}문장 이내 한국어 요약으로 정리\n"
            "4) 마지막에 핵심 키워드 3개를 bullet로 제시\n"
        ),
        arguments=(
            PromptArgumentSpec(
                name="path",
                description="요약할 HWPX 파일 경로",
                schema={"type": "string", "minLength": 1},
            ),
            PromptArgumentSpec(
                name="summaryStyle",
                description="요약 톤(예: 임원 보고, 일반 설명)",
                schema={"type": "string", "default": "일반 설명"},
            ),
            PromptArgumentSpec(
                name="maxSentences",
                description="최대 요약 문장 수",
                schema={"type": "string", "default": "5"},
            ),
        ),
        tool_bindings=(
            ToolBinding("open_info", '{"path": "{path}"}'),
            ToolBinding("text_extract_report", '{"path": "{path}", "mode": "plain"}'),
        ),
        example_input={"path": "sample.hwpx", "summaryStyle": "임원 보고", "maxSentences": "4"},
        example_output="문서의 목적/핵심 내용 4문장 요약 + 키워드 3개",
    ),
    PromptTemplate(
        prompt_id="table_to_csv",
        version="v1",
        title="표 추출 → CSV",
        description="특정 표를 추출해 CSV 텍스트로 변환합니다.",
        template=(
            "다음 절차로 표를 CSV로 변환해 주세요.\n"
            "1) 도구 `get_table_cell_map` 호출\n"
            "   - arguments: {{\"path\": \"{path}\", \"tableIndex\": {tableIndex}}}\n"
            "2) 반환된 grid를 순회하면서 anchor 기준으로 값을 정규화\n"
            "3) {delimiter} 구분자를 사용해 CSV 본문 생성\n"
            "4) 첫 줄은 헤더로 간주: {headerPolicy}\n"
            "5) 최종 출력은 코드블록 없이 순수 CSV 텍스트만 반환\n"
        ),
        arguments=(
            PromptArgumentSpec(
                name="path",
                description="표를 읽을 HWPX 파일 경로",
                schema={"type": "string", "minLength": 1},
            ),
            PromptArgumentSpec(
                name="tableIndex",
                description="추출할 표의 0-기반 인덱스",
                schema={"type": "string", "pattern": "^[0-9]+$"},
            ),
            PromptArgumentSpec(
                name="delimiter",
                description="CSV 구분자",
                schema={"type": "string", "default": ","},
            ),
            PromptArgumentSpec(
                name="headerPolicy",
                description="헤더 처리 규칙(예: 첫 행 헤더 유지)",
                schema={"type": "string", "default": "첫 행 헤더 유지"},
            ),
        ),
        tool_bindings=(
            ToolBinding("get_table_cell_map", '{"path": "{path}", "tableIndex": {tableIndex}}'),
        ),
        example_input={
            "path": "sample.hwpx",
            "tableIndex": "0",
            "delimiter": ",",
            "headerPolicy": "첫 행 헤더 유지",
        },
        example_output="항목,수량,비고\n사과,10,국내\n배,5,수입",
    ),
    PromptTemplate(
        prompt_id="document_lint",
        version="v1",
        title="문서 린트",
        description="문서 텍스트 규칙 위반 항목을 점검합니다.",
        template=(
            "아래 린트 절차를 수행해 주세요.\n"
            "1) 도구 `lint_text_conventions` 호출\n"
            "   - arguments: {{\"path\": \"{path}\", \"rules\": {{\"maxLineLen\": {maxLineLen}, \"forbidPatterns\": [{forbidPatterns}]}}}}\n"
            "2) warnings를 심각도(높음/중간/낮음)로 재분류\n"
            "3) 각 항목을 `문단 번호 | 문제 | 개선 제안` 형식 표로 출력\n"
            "4) 마지막에 수정 우선순위 Top3를 제시\n"
        ),
        arguments=(
            PromptArgumentSpec(
                name="path",
                description="린트할 HWPX 파일 경로",
                schema={"type": "string", "minLength": 1},
            ),
            PromptArgumentSpec(
                name="maxLineLen",
                description="권장 최대 줄 길이",
                schema={"type": "string", "default": "120", "pattern": "^[0-9]+$"},
            ),
            PromptArgumentSpec(
                name="forbidPatterns",
                description='금지 패턴 목록(예: "TODO", "임시")를 따옴표 포함 콤마로 전달',
                schema={"type": "string", "default": '"TODO", "임시"'},
            ),
        ),
        tool_bindings=(
            ToolBinding(
                "lint_text_conventions",
                '{"path": "{path}", "rules": {"maxLineLen": {maxLineLen}, "forbidPatterns": [{forbidPatterns}]}}',
            ),
        ),
        example_input={
            "path": "sample.hwpx",
            "maxLineLen": "100",
            "forbidPatterns": '"TODO", "TBD"',
        },
        example_output="문단 번호 | 문제 | 개선 제안\n12 | TODO 잔존 | 실제 작업 상태로 교체",
    ),
)

PROMPT_BY_NAME: dict[str, PromptTemplate] = {prompt.name: prompt for prompt in PROMPT_TEMPLATES}


def list_prompts() -> list[types.Prompt]:
    return [prompt.to_prompt() for prompt in PROMPT_TEMPLATES]


def get_prompt(name: str, arguments: Mapping[str, str] | None) -> types.GetPromptResult:
    prompt = PROMPT_BY_NAME.get(name)
    if prompt is None:
        raise KeyError(name)
    return prompt.render(arguments)
