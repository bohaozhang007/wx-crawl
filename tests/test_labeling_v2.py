from __future__ import annotations

import csv
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

from src.labeling.schema import load_tree_spec, validate_payload
from src.storage import db


ROOT = Path(__file__).resolve().parents[1]
SELECTOR_PATH = ROOT / "skill" / "article-label-export" / "scripts" / "select_articles.py"


def label_payload(
    decision: str,
    reason_code: str,
    path: list[str],
    application_type: str,
    domains: list[str],
) -> dict:
    evidence = [
        {
            "type": "negative" if decision == "DROP" else "missing_evidence",
            "text": "用于验证终止节点的文章证据",
            "location": "正文第一段",
        }
    ]
    if decision == "KEEP":
        evidence = [
            {"type": "solicitation", "text": "请于8月30日前提交申报材料", "location": "申报要求"},
            {"type": "domain", "text": "研发多模态大模型训练方法", "location": "研究内容"},
        ]
    return {
        "schema_version": 2,
        "tree_version": load_tree_spec()["version"],
        "decision": decision,
        "decision_path": path,
        "reason_code": reason_code,
        "reason": "这是针对当前文章内容给出的具体判断原因。",
        "evidence": evidence,
        "application_type": application_type,
        "domains": domains,
    }


class LabelSchemaV2Test(unittest.TestCase):
    def test_keep_drop_and_review_are_valid(self) -> None:
        keep = label_payload(
            "KEEP",
            "K1",
            ["E1:PASS", "O1:PASS", "O2:PASS", "R1:PASS", "A1:PASS", "T1:PASS", "D1:PASS", "K1"],
            "科研项目申请",
            ["大模型"],
        )
        drop = label_payload(
            "DROP",
            "D1-D4",
            ["E1:PASS", "O1:PASS", "O2:PASS", "R1:PASS", "A1:PASS", "T1:PASS", "D1:D1-D4"],
            "科研指南申请",
            [],
        )
        review = label_payload("REVIEW", "E1-R2", ["E1:E1-R2"], "都不是", [])
        self.assertEqual(validate_payload(keep), [])
        self.assertEqual(validate_payload(drop), [])
        self.assertEqual(validate_payload(review), [])

    def test_legacy_and_mismatched_terminal_are_rejected(self) -> None:
        legacy = {"application_type": "科研项目申请", "domains": ["大模型"]}
        self.assertTrue(validate_payload(legacy))
        payload = label_payload("KEEP", "O1-D1", ["O1:O1-D1"], "科研项目申请", ["大模型"])
        errors = validate_payload(payload)
        self.assertTrue(any("belongs to DROP" in error for error in errors))

    def test_summary_is_optional_for_existing_v2_but_validated_when_present(self) -> None:
        payload = label_payload(
            "KEEP",
            "K1",
            ["E1:PASS", "O1:PASS", "O2:PASS", "R1:PASS", "A1:PASS", "T1:PASS", "D1:PASS", "K1"],
            "科研项目申请",
            ["大模型"],
        )
        self.assertEqual(validate_payload(payload), [])
        self.assertTrue(validate_payload(payload, require_summary=True))
        payload["summary"] = "文章发布大模型科研项目申报通知，明确申报期限和研究任务。"
        self.assertEqual(validate_payload(payload, require_summary=True), [])
        payload["summary"] = "   "
        self.assertTrue(validate_payload(payload, require_summary=True))


class SelectorDecisionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.articles_root = self.root / "articles"
        self.record_root = self.root / "record"
        self.run_dir = self.record_root / "2026_01_01_00_00_00"
        self.run_dir.mkdir(parents=True)

        spec = importlib.util.spec_from_file_location("selector_v2_test", SELECTOR_PATH)
        assert spec is not None and spec.loader is not None
        self.selector = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.selector)
        self.selector.ARTICLES_ROOT = self.articles_root
        self.selector.RECORD_ROOT = self.record_root

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def add_article(self, title: str, label: dict) -> None:
        article_dir = self.articles_root / "1_MP_WXS_123_TestAccount" / f"2026_01_01_00_00_00_{title}"
        article_dir.mkdir(parents=True)
        (article_dir / "metadata.json").write_text(
            json.dumps(
                {
                    "title": title,
                    "url": f"https://mp.weixin.qq.com/s/{title}",
                    "publish_time": 1767225600,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        (article_dir / "content.txt").write_text("测试正文", encoding="utf-8")
        (article_dir / "label.json").write_text(json.dumps(label, ensure_ascii=False), encoding="utf-8")

    def test_selector_only_selects_keep_and_preserves_reasons_in_ledger(self) -> None:
        self.add_article(
            "keep",
            label_payload(
                "KEEP",
                "K1",
                ["E1:PASS", "O1:PASS", "O2:PASS", "R1:PASS", "A1:PASS", "T1:PASS", "D1:PASS", "K1"],
                "科研项目申请",
                ["大模型"],
            ),
        )
        self.add_article(
            "drop",
            label_payload("DROP", "O1-D1", ["E1:PASS", "O1:O1-D1"], "都不是", []),
        )
        self.add_article(
            "review",
            label_payload("REVIEW", "E1-R2", ["E1:E1-R2"], "都不是", []),
        )
        with (self.run_dir / "article_details.csv").open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=["公众号名称", "爬取的文章名称", "文章发布时间"])
            writer.writeheader()
            for title in ("keep", "drop", "review"):
                writer.writerow({"公众号名称": "TestAccount", "爬取的文章名称": title, "文章发布时间": ""})

        result = self.selector.select_matches(self.run_dir)
        self.assertEqual([item["title"] for item in result["articles"]], ["keep"])
        ledger = json.loads((self.run_dir / "labeling_ledger.json").read_text(encoding="utf-8"))
        self.assertEqual(ledger["schema_version"], 2)
        self.assertEqual(ledger["candidates"], len(ledger["entries"]))
        self.assertEqual((ledger["keep"], ledger["drop"], ledger["review"]), (1, 1, 1))
        entries = {item["title"]: item for item in ledger["entries"]}
        self.assertEqual(entries["keep"]["selection"], "selected")
        self.assertEqual(entries["drop"]["reason_code"], "O1-D1")
        self.assertEqual(entries["review"]["selection"], "review")


class PruneProtectionTest(unittest.TestCase):
    def test_full_cleanup_only_targets_explicit_drop(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "articles"
            database = Path(temp) / "articles.sqlite3"
            original_root = db.ARTICLES_ROOT
            db.ARTICLES_ROOT = root
            try:
                paths: dict[str, Path] = {}
                for title, payload in {
                    "drop": label_payload("DROP", "O1-D1", ["E1:PASS", "O1:O1-D1"], "都不是", []),
                    "review": label_payload("REVIEW", "E1-R2", ["E1:E1-R2"], "都不是", []),
                }.items():
                    article_dir = root / "1_MP_WXS_123_TestAccount" / f"2026_01_01_00_00_00_{title}"
                    article_dir.mkdir(parents=True)
                    (article_dir / "content.txt").write_text("正文", encoding="utf-8")
                    (article_dir / "metadata.json").write_text(
                        json.dumps(
                            {"title": title, "url": f"https://mp.weixin.qq.com/s/{title}"},
                            ensure_ascii=False,
                        ),
                        encoding="utf-8",
                    )
                    (article_dir / "label.json").write_text(
                        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
                    )
                    paths[title] = article_dir

                db.init_database(database)
                preview = db.prune_all_unselected(database, confirm=False)
                self.assertEqual(preview["would_delete"], [str(paths["drop"])])
                self.assertEqual(
                    preview["protected"],
                    [{"article_dir": str(paths["review"]), "decision": "REVIEW"}],
                )
            finally:
                db.ARTICLES_ROOT = original_root


if __name__ == "__main__":
    unittest.main()
