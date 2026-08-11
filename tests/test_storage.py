from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from src.storage import db


class StorageTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name) / "articles"
        self.article = self.root / "1_MP_WXS_123_TestAccount" / "2026_01_01_00_00_00_Test"
        self.article.mkdir(parents=True)
        (self.article / "metadata.json").write_text(
            json.dumps(
                {
                    "title": "测试文章",
                    "url": "https://mp.weixin.qq.com/s/test",
                    "publish_time": 1767225600,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        (self.article / "content.txt").write_text("正文内容", encoding="utf-8")
        self.report = Path(self.temp_dir.name) / "filtered_articles.json"
        self.report.write_text(
            json.dumps(
                {
                    "run_id": "run-test",
                    "articles": [
                        {
                            "article_dir": str(self.article),
                            "title": "测试文章",
                            "url": "https://mp.weixin.qq.com/s/test",
                            "publish_time": 1767225600,
                            "application_type": "科研项目申请",
                            "domains": ["具身智能", "机器人"],
                            "summary": "测试摘要",
                        }
                    ],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        self.database = Path(self.temp_dir.name) / "articles.sqlite3"
        self.original_root = db.ARTICLES_ROOT
        db.ARTICLES_ROOT = self.root

    def tearDown(self) -> None:
        db.ARTICLES_ROOT = self.original_root
        self.temp_dir.cleanup()

    def test_import_is_idempotent_and_queryable(self) -> None:
        first = db.import_report(self.report, self.database)
        second = db.import_report(self.report, self.database)

        self.assertEqual(first["inserted"], 1)
        self.assertEqual(second["updated"], 1)
        rows = db.query_articles(self.database, domain="具身智能")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["domains"], ["具身智能", "机器人"])
        self.assertEqual(rows[0]["content_text"], "正文内容")

    def test_delivery_state(self) -> None:
        db.import_report(self.report, self.database)
        article_id = db.query_articles(self.database)[0]["id"]
        self.assertEqual(len(db.query_articles(self.database, channel="dingtalk", pending_only=True)), 1)
        db.mark_delivered(self.database, [article_id], "dingtalk")
        self.assertEqual(db.query_articles(self.database, channel="dingtalk", pending_only=True), [])

    def test_invalid_report_does_not_write(self) -> None:
        invalid = json.loads(self.report.read_text(encoding="utf-8"))
        invalid["articles"][0]["domains"] = []
        self.report.write_text(json.dumps(invalid, ensure_ascii=False), encoding="utf-8")
        with self.assertRaises(db.StorageError):
            db.import_report(self.report, self.database)
        self.assertFalse(self.database.exists())


if __name__ == "__main__":
    unittest.main()

