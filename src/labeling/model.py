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


class LabelOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[2]
    tree_version: str
    decision: Literal["KEEP", "DROP", "REVIEW"]
    decision_path: list[str]
    reason_code: str
    reason: str
    summary: str = Field(min_length=1, max_length=500)
    evidence: list[EvidenceOutput]
    application_type: Literal["科研项目申请", "科研指南申请", "都不是"]
    domains: list[Literal["无人机", "卫星", "具身智能", "大模型", "空天", "机器人", "机械臂"]]


class LabelModel(Protocol):
    async def label(self, system_prompt: str, article_prompt: str, feedback: str = "") -> dict:
        ...


class OpenAILabelModel:
    def __init__(self, config: LabelingConfig) -> None:
        self.config = config
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

    async def label(self, system_prompt: str, article_prompt: str, feedback: str = "") -> dict:
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
                text_format=LabelOutput,
            )
            parsed = response.output_parsed
            if parsed is None:
                raise RuntimeError("model returned no parsed label (possibly a refusal)")
            return parsed.model_dump(mode="json")

        schema = json.dumps(LabelOutput.model_json_schema(), ensure_ascii=False)
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
            max_tokens=4096,
        )
        raw = response.choices[0].message.content
        if not raw:
            raise RuntimeError("model returned empty JSON content")
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"model returned invalid JSON: {exc}") from exc
        if not isinstance(payload, dict):
            raise RuntimeError("model JSON root must be an object")
        return payload
