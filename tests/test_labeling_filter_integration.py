from __future__ import annotations

import csv
import importlib.util
import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

from src.labeling.runner import run_labeling
from src.labeling.schema import load_tree_spec


REPO_ROOT = Path(__file__).resolve().parents[1]
SELECTOR_PATH = REPO_ROOT / "skill/article-label-export/scripts/select_articles.py"


def load_selector_module():
    spec = importlib.util.spec_from_file_location("article_label_export_test", SELECTOR_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeKeepModel:
    async def label(self, system_prompt: str, article_prompt: str, feedback: str = "") -> dict:
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


class PythonLabelFilterIntegrationTest(unittest.IsolatedAsyncioTestCase):
    async def test_new_labeler_output_is_consumed_by_python_selector(self) -> None:
        selector = load_selector_module()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            articles_root = root / "articles"
            record_root = root / "record"
            run_dir = record_root / "2026_08_16_12_00_00"
            article_dir = articles_root / "1_MP_WXS_123_TestAccount" / "2026_08_16_10_00_00_TestArticle"
            article_dir.mkdir(parents=True)
            run_dir.mkdir(parents=True)

            publish_time = int(
                datetime(2026, 8, 16, 10, 0, 0, tzinfo=ZoneInfo("Asia/Shanghai")).timestamp()
            )
            (article_dir / "metadata.json").write_text(
                json.dumps(
                    {
                        "title": "大模型科研项目申报",
                        "url": "https://mp.weixin.qq.com/s/test-python-pipeline",
                        "publish_time": publish_time,
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            (article_dir / "content.txt").write_text(
                "申报要求：请于8月30日前提交申报材料。研究内容：研发多模态大模型训练方法。",
                encoding="utf-8",
            )
            with (run_dir / "article_details.csv").open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=("公众号名称", "爬取的文章名称", "文章发布时间"))
                writer.writeheader()
                writer.writerow(
                    {
                        "公众号名称": "TestAccount",
                        "爬取的文章名称": "大模型科研项目申报",
                        "文章发布时间": "2026_08_16_10_00_00",
                    }
                )

            labeled = await run_labeling(
                [article_dir], FakeKeepModel(), concurrency=1, max_retries=0
            )
            self.assertEqual(labeled["labeled"], 1)

            with patch.object(selector, "ARTICLES_ROOT", articles_root), patch.object(
                selector, "RECORD_ROOT", record_root
            ):
                selected = selector.select_matches(run_dir)

            self.assertEqual(selected["count"], 1)
            self.assertEqual(selected["articles"][0]["reason_code"], "K1")
            ledger = json.loads((run_dir / "labeling_ledger.json").read_text(encoding="utf-8"))
            self.assertEqual(ledger["entries"][0]["selection"], "selected")
            self.assertEqual(ledger["entries"][0]["domains"], ["大模型"])


if __name__ == "__main__":
    unittest.main()
