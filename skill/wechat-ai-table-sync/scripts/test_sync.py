import importlib.util
import json
import unittest
from pathlib import Path
from unittest.mock import Mock

SCRIPT = Path(__file__).with_name("sync_articles.py")
spec = importlib.util.spec_from_file_location("sync_articles", SCRIPT)
assert spec is not None and spec.loader is not None
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class SyncTest(unittest.TestCase):
    def test_db_row_maps_to_existing_ai_table_fields(self):
        row = {
            "id": 7,
            "url": "https://mp.weixin.qq.com/s/x",
            "title": "标题",
            "account_name": "公众号",
            "account_id": "MP_WXS_1",
            "publish_time": 1760000000,
            "application_type": "科研项目申请",
            "domains": ["具身智能", "机器人"],
            "summary": "摘要",
            "content_text": "正文",
            "cover_url": "https://img/x.jpg",
            "crawl_run": "run-1",
            "created_at": 1760000001,
            "updated_at": 1760000002,
        }
        fields = module.to_table_fields(row)
        self.assertEqual(fields, {
            "id": "7", "url": row["url"], "title": "标题",
            "account_name": "公众号", "publish_time": "1760000000",
            "application_type": "科研项目申请", "summary": "摘要",
            "content_text": "正文", "domains": "具身智能,机器人",
        })

    def test_incremental_sync_inserts_missing_and_updates_changed_rows(self):
        api = Mock()
        api.list_records.return_value = [
            {"id": "remote-1", "fields": {"id": "1", "title": "旧标题"}},
            {"id": "remote-2", "fields": {"id": "2", "url": "u2", "title": "相同", "account_name": "a", "publish_time": "2", "application_type": "科研项目申请", "domains": "", "summary": "", "content_text": ""}},
        ]
        rows = [
            {"id": 1, "title": "新标题", "url": "u1", "account_name": "a", "publish_time": 1,
             "application_type": "科研项目申请", "domains": [], "summary": "", "content_text": ""},
            {"id": 2, "title": "相同", "url": "u2", "account_name": "a", "publish_time": 2,
             "application_type": "科研项目申请", "domains": [], "summary": "", "content_text": ""},
            {"id": 3, "title": "新增", "url": "u3", "account_name": "a", "publish_time": 3,
             "application_type": "科研指南申请", "domains": [], "summary": "", "content_text": ""},
        ]
        result = module.sync_rows(api, rows, mode="incremental", batch_size=50)
        self.assertEqual(result, {"inserted": 1, "updated": 1, "unchanged": 1})
        api.update_records.assert_called_once()
        api.insert_records.assert_called_once()


if __name__ == "__main__":
    unittest.main()
