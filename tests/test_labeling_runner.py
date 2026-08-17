from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import patch

from src.labeling.config import load_labeling_config
from src.labeling.runner import discover_run_article_dirs, run_labeling
from src.labeling.schema import load_tree_spec, read_label


def keep_payload() -> dict:
    return {
        "schema_version": 2,
        "tree_version": load_tree_spec()["version"],
        "decision": "KEEP",
        "decision_path": [
            "E1:PASS",
            "O1:PASS",
            "O2:PASS",
            "R1:PASS",
            "A1:PASS",
            "T1:PASS",
            "D1:PASS",
            "K1",
        ],
        "reason_code": "K1",
        "reason": "文章开放科研项目申报，任务直接要求研发多模态大模型。",
        "evidence": [
            {"type": "solicitation", "text": "请于8月30日前提交申报材料", "location": "申报要求"},
            {"type": "domain", "text": "研发多模态大模型训练方法", "location": "研究内容"},
        ],
        "application_type": "科研项目申请",
        "domains": ["大模型"],
    }


class FakeModel:
    def __init__(self, outputs: list[dict]) -> None:
        self.outputs = outputs
        self.calls = 0
        self.feedback: list[str] = []

    async def label(self, system_prompt: str, article_prompt: str, feedback: str = "") -> dict:
        self.feedback.append(feedback)
        output = self.outputs[min(self.calls, len(self.outputs) - 1)]
        self.calls += 1
        return output


class LabelingConfigTest(unittest.TestCase):
    def test_environment_overrides_yaml_without_storing_key(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "config.yaml"
            path.write_text(
                "labeling:\n"
                "  model: yaml-model\n"
                "  base_url: https://yaml.invalid/v1\n"
                "  concurrency: 2\n"
                "  timeout_seconds: 10\n"
                "  max_retries: 1\n",
                encoding="utf-8",
            )
            with patch.dict(
                "os.environ",
                {
                    "LABEL_API_KEY": "secret",
                    "LABEL_MODEL": "env-model",
                    "LABEL_BASE_URL": "https://env.invalid/v1",
                },
                clear=True,
            ):
                config = load_labeling_config(path)
            self.assertEqual(config.api_key, "secret")
            self.assertEqual(config.model, "env-model")
            self.assertEqual(config.base_url, "https://env.invalid/v1")
            self.assertEqual(config.concurrency, 2)

    def test_defaults_to_hermes_provider_model_base_url_and_key(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            project_config = root / "project.yaml"
            project_config.write_text(
                "labeling:\n"
                "  provider: ''\n"
                "  model: ''\n"
                "  base_url: ''\n"
                "  concurrency: 2\n"
                "  timeout_seconds: 10\n"
                "  max_retries: 1\n",
                encoding="utf-8",
            )
            hermes_home = root / "hermes"
            hermes_home.mkdir()
            (hermes_home / "config.yaml").write_text(
                "model:\n"
                "  provider: deepseek\n"
                "  default: deepseek-v4-flash\n"
                "  base_url: https://api.deepseek.com/v1\n",
                encoding="utf-8",
            )
            (hermes_home / ".env").write_text("DEEPSEEK_API_KEY=hermes-secret\n", encoding="utf-8")
            with patch.dict("os.environ", {}, clear=True):
                config = load_labeling_config(project_config, hermes_home=hermes_home)
            self.assertEqual(config.provider, "deepseek")
            self.assertEqual(config.model, "deepseek-v4-flash")
            self.assertEqual(config.base_url, "https://api.deepseek.com/v1")
            self.assertEqual(config.api_key, "hermes-secret")
            self.assertEqual(config.api_key_source, "DEEPSEEK_API_KEY")
            self.assertEqual(config.api_style, "chat_completions_json")
            self.assertEqual(config.source, "hermes")


class LabelingRunnerTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.article_dir = Path(self.temp_dir.name) / "1_MP_WXS_123_Test" / "2026_01_01_Test"
        self.article_dir.mkdir(parents=True)
        (self.article_dir / "metadata.json").write_text(
            json.dumps({"title": "大模型科研项目申报"}, ensure_ascii=False), encoding="utf-8"
        )
        (self.article_dir / "content.txt").write_text(
            "申报要求：请于8月30日前提交申报材料。研究内容：研发多模态大模型训练方法。",
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    async def test_writes_valid_label_and_then_skips_it(self) -> None:
        model = FakeModel([keep_payload()])
        first = await run_labeling([self.article_dir], model, concurrency=1, max_retries=0)
        self.assertEqual(first["labeled"], 1)
        label, errors = read_label(self.article_dir / "label.json")
        self.assertEqual(errors, [])
        self.assertEqual(label["decision"], "KEEP")

        second = await run_labeling([self.article_dir], model, concurrency=1, max_retries=0)
        self.assertEqual(second["skipped_valid"], 1)
        self.assertEqual(model.calls, 1)

    async def test_invalid_evidence_is_returned_as_retry_feedback(self) -> None:
        invalid = keep_payload()
        invalid["evidence"][1]["text"] = "正文中不存在的技术证据"
        model = FakeModel([invalid, keep_payload()])
        result = await run_labeling([self.article_dir], model, concurrency=1, max_retries=1)
        self.assertEqual(result["labeled"], 1)
        self.assertEqual(model.calls, 2)
        self.assertIn("not present in the article", model.feedback[1])

    async def test_failure_does_not_overwrite_legacy_label(self) -> None:
        legacy = {"application_type": "科研项目申请", "domains": ["大模型"]}
        label_path = self.article_dir / "label.json"
        label_path.write_text(json.dumps(legacy, ensure_ascii=False), encoding="utf-8")
        invalid = keep_payload()
        invalid["reason_code"] = "O1-D1"
        model = FakeModel([invalid])
        result = await run_labeling([self.article_dir], model, concurrency=1, max_retries=0)
        self.assertEqual(result["failed"], 1)
        self.assertEqual(json.loads(label_path.read_text(encoding="utf-8")), legacy)

    def test_run_scope_uses_python_selector_candidates(self) -> None:
        record_root = Path(self.temp_dir.name) / "record"
        run_dir = record_root / "2026_08_16_12_00_00"
        run_dir.mkdir(parents=True)
        (run_dir / "article_details.csv").write_text("公众号名称,爬取的文章名称,文章发布时间\n", encoding="utf-8")
        stdout = json.dumps(
            {"count": 1, "articles": [{"article_dir": str(self.article_dir)}]},
            ensure_ascii=False,
        )
        with patch(
            "src.labeling.runner.subprocess.run",
            return_value=CompletedProcess(args=[], returncode=0, stdout=stdout, stderr=""),
        ) as mocked:
            result = discover_run_article_dirs(
                run_dir,
                record_root=record_root,
                articles_root=Path(self.temp_dir.name),
            )
        self.assertEqual(result, [self.article_dir.resolve()])
        command = mocked.call_args.args[0]
        self.assertIn("candidates", command)
        self.assertEqual(command[-1], str(run_dir.resolve()))


if __name__ == "__main__":
    unittest.main()
