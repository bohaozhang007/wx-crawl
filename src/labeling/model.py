from __future__ import annotations

import json
from typing import Literal, Protocol

import httpx
from openai import AsyncOpenAI
from pydantic import BaseModel, ConfigDict, Field

from .config import LabelingConfig


class EvidenceOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["solicitation", "research_task", "domain", "negative", "missing_evidence"]
    text: str
    location: str


class DeadlineOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["confirmed", "ambiguous", "missing"]
    raw_text: str
    timestamp: int | None
    timezone: Literal["Asia/Shanghai"]


class ImportanceFactorsOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    deadline_urgency: Literal["high", "medium", "low", "expired", "unknown"]
    project_significance: Literal["high", "medium", "low", "unknown"]
    amount_level: Literal["high", "medium", "low", "unknown"]
    amount_raw_text: str
    domain_fit: Literal["strong", "medium", "weak", "none"]


class ImportanceOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    level: Literal["high", "medium", "low", "not_applicable"]
    reason: str = Field(min_length=1, max_length=500)
    factors: ImportanceFactorsOutput


class GeographyOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["eligible", "out_of_scope", "unclear"]
    regions: list[Literal["national", "beijing", "zhejiang", "other", "unknown"]]
    evidence: str


class EvidenceValue(BaseModel):
    model_config = ConfigDict(extra='forbid')
    value: str | None
    evidence: str

class ProjectIntakeOutput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    version: Literal[1]
    project_name: EvidenceValue
    authority: EvidenceValue
    amount_wan: EvidenceValue
    start_date: EvidenceValue
    end_date: EvidenceValue
    requirements: EvidenceValue
    deliverables: EvidenceValue


class LabelOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[2]
    tree_version: str
    profile_version: str
    geography: GeographyOutput
    project_intake: ProjectIntakeOutput
    decision: Literal["KEEP", "DROP", "REVIEW"]
    decision_path: list[str]
    reason_code: str
    reason: str
    summary: str = Field(min_length=1, max_length=500)
    deadline: DeadlineOutput
    importance: ImportanceOutput
    evidence: list[EvidenceOutput]
    application_type: Literal["科研项目申请", "科研指南申请", "都不是"]
    domains: list[Literal["无人机", "具身智能", "大模型", "空天", "机器人", "机械臂"]]


class LabelModel(Protocol):
    async def label(self, system_prompt: str, article_prompt: str, feedback: str = "") -> tuple[dict, dict]:
        """Return (payload, usage). usage carries prompt/completion/cached tokens."""
        ...


def _usage_from_response(response) -> dict:
    """Extract token usage from an OpenAI SDK response object (chat or responses)."""
    usage = getattr(response, "usage", None)
    if usage is None:
        return {}
    details = getattr(usage, "prompt_tokens_details", None) or {}
    cached = int(getattr(details, "cached_tokens", 0) or 0)
    return {
        "prompt_tokens": int(getattr(usage, "prompt_tokens", 0) or 0),
        "completion_tokens": int(getattr(usage, "completion_tokens", 0) or 0),
        "total_tokens": int(getattr(usage, "total_tokens", 0) or 0),
        "cached_tokens": cached,
    }


class OpenAILabelModel:
    output_model = LabelOutput

    def __init__(self, config: LabelingConfig, *, max_output_tokens: int | None = None) -> None:
        if max_output_tokens is None:
            max_output_tokens = config.max_output_tokens
        if max_output_tokens < 1:
            raise ValueError("max_output_tokens must be positive")
        self.config = config
        self.max_output_tokens = max_output_tokens
        self.model = config.model
        self.client = AsyncOpenAI(
            api_key=config.api_key,
            base_url=config.base_url,
            timeout=config.timeout_seconds,
            max_retries=0,
            http_client=httpx.AsyncClient(
                timeout=config.timeout_seconds,
                trust_env=False,
            ),
        )

    async def label(self, system_prompt: str, article_prompt: str, feedback: str = "") -> tuple[dict, dict]:
        content = article_prompt
        if feedback:
            content += (
                "\n\n<validation_feedback>\n"
                "上一次输出未通过本地校验。重新阅读全文并修正以下问题：\n"
                f"{feedback}\n"
                "</validation_feedback>"
            )
        if self.config.api_style == "responses_parse":
            response = await self.client.responses.parse(
                model=self.model,
                input=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": content},
                ],
                text_format=self.output_model,
            )
            parsed = response.output_parsed
            if parsed is None:
                raise RuntimeError("model returned no parsed label (possibly a refusal)")
            return parsed.model_dump(mode="json"), _usage_from_response(response)

        schema = json.dumps(self.output_model.model_json_schema(), ensure_ascii=False)
        response = await self.client.chat.completions.create(
            model=self.model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        system_prompt
                        + "\n只输出一个合法 JSON 对象，不要使用 Markdown 代码块。"
                        + "输出必须符合以下 JSON Schema：\n"
                        + schema
                    ),
                },
                {"role": "user", "content": content},
            ],
            response_format={"type": "json_object"},
            max_tokens=self.max_output_tokens,
        )
        raw = response.choices[0].message.content
        if not raw:
            finish_reason = getattr(response.choices[0], "finish_reason", "unknown")
            raise RuntimeError(f"model returned empty JSON content (finish_reason={finish_reason})")
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"model returned invalid JSON: {exc}") from exc
        if not isinstance(payload, dict):
            raise RuntimeError("model JSON root must be an object")
        return payload, _usage_from_response(response)


class OpenAIProjectIntakeModel(OpenAILabelModel):
    output_model = ProjectIntakeOutput
