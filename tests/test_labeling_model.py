from __future__ import annotations

import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from src.labeling.config import LabelingConfig
from src.labeling.model import OpenAILabelModel


class FakeChatCompletions:
    def __init__(self) -> None:
        self.kwargs = None

    async def create(self, **kwargs):
        self.kwargs = kwargs
        message = SimpleNamespace(content=json.dumps({"schema_version": 2}))
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])


class FakeClient:
    def __init__(self) -> None:
        self.completions = FakeChatCompletions()
        self.chat = SimpleNamespace(completions=self.completions)


class LabelingModelCompatibilityTest(unittest.IsolatedAsyncioTestCase):
    async def test_deepseek_uses_chat_completions_json_output(self) -> None:
        client = FakeClient()
        config = LabelingConfig(
            api_key="secret",
            api_key_source="DEEPSEEK_API_KEY",
            provider="deepseek",
            model="deepseek-v4-flash",
            base_url="https://api.deepseek.com/v1",
            api_style="chat_completions_json",
            source="hermes",
            concurrency=2,
            timeout_seconds=30,
            max_retries=1,
        )
        with patch("src.labeling.model.AsyncOpenAI", return_value=client):
            model = OpenAILabelModel(config)
        payload = await model.label("system", "article")
        self.assertEqual(payload, {"schema_version": 2})
        self.assertEqual(
            client.completions.kwargs["response_format"], {"type": "json_object"}
        )
        self.assertIn("JSON Schema", client.completions.kwargs["messages"][0]["content"])
        self.assertEqual(client.completions.kwargs["model"], "deepseek-v4-flash")


if __name__ == "__main__":
    unittest.main()
