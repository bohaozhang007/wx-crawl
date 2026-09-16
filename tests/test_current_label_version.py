import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.labeling.schema import validate_payload, load_tree_spec
from src.labeling.current_version import require_current_selection
from tests.test_labeling_runner import keep_payload, FakeModel
from src.labeling.runner import run_labeling
from tests.test_storage import StorageTest


class VersionTests(unittest.IsolatedAsyncioTestCase):
    async def test_old_tree_is_relabeled_not_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)
            old=keep_payload();old['tree_version']='1.0'
            (p/'label.json').write_text(json.dumps(old))
            (p/'metadata.json').write_text('{}')
            (p/'content.txt').write_text('请于8月30日前提交申报材料。研发多模态大模型训练方法。')
            model=FakeModel([keep_payload()])
            result=await run_labeling([p],model,1,0)
            self.assertEqual(result['skipped_valid'],0)
            self.assertEqual(result['labeled'],1)
            self.assertEqual(model.calls,1)

    def test_schema_v1_and_old_tree_are_rejected(self):
        for key,value in [('schema_version',1),('tree_version','1.0'),('profile_version','1.1'),('profile_version',None)]:
            payload=keep_payload();payload[key]=value
            self.assertTrue(any(key in error for error in validate_payload(payload)))

    def test_tree_change_is_visible_in_same_process(self):
        source=Path(load_tree_spec()['path']).read_text()
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'tree.md';p.write_text(source)
            first=load_tree_spec(p)['version']
            p.write_text(source.replace(f'Tree version: `{first}`','Tree version: `test-next`'))
            self.assertEqual(load_tree_spec(p)['version'],'test-next')

    def test_sync_rejects_old_source_label(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);p=root/'account'/'article';p.mkdir(parents=True)
            row={'id':1,'url':'https://mp.weixin.qq.com/s/test','application_type':'科研项目申请','domains':['大模型'],'summary':keep_payload()['summary']}
            (p/'metadata.json').write_text(json.dumps({'url':row['url']}))
            label=keep_payload();label['tree_version']='1.0';(p/'label.json').write_text(json.dumps(label))
            with self.assertRaises(RuntimeError):require_current_selection([row],root)
            (p/'label.json').write_text(json.dumps(keep_payload()))
            require_current_selection([row],root)

    def test_pending_labels_cannot_become_empty_completed_report(self):
        script=Path(__file__).resolve().parents[1]/'skill/article-label-export/scripts/select_articles.py'
        spec=importlib.util.spec_from_file_location('strict_selector',script)
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp);out=p/'filtered_articles.json'
            with patch.object(module,'select_matches',return_value={'pending_count':1,'articles':[]}):
                with self.assertRaises(SystemExit):module.write_report(p,None,out)
            self.assertFalse(out.exists())


class ImportVersionTest(StorageTest):
    def test_import_rechecks_source_version_before_transaction(self):
        label=json.loads((self.article/'label.json').read_text());label['tree_version']='1.0'
        (self.article/'label.json').write_text(json.dumps(label))
        from src.storage.db import import_report,StorageError
        with self.assertRaises(StorageError):import_report(self.report,self.database)
        self.assertFalse(self.database.exists())


class CompletionVersionTests(unittest.TestCase):
    def test_old_completion_and_coverage_do_not_skip_batch(self):
        from src.labeling.schema import current_contract
        script = Path(__file__).resolve().parents[1]/'skill/wechat-pipeline-orchestration/scripts/pipeline_state.py'
        spec = importlib.util.spec_from_file_location('version_state', script)
        module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); run = root/'run'; run.mkdir()
            state = {'schema_version': 1, 'status': 'completed', 'stages': dict.fromkeys(['label','report','database','cleanup','ai_table_sync'], 'completed')}
            (run/module.STATE_NAME).write_text(json.dumps(state))
            coverage = {'covered_run_ids': ['run']}
            (root/'pipeline_coverage.json').write_text(json.dumps(coverage))
            with patch.object(module, 'RECORD_ROOT', root):
                self.assertFalse(module.is_completed(run))
                (run/'filtered_articles.json').write_text('{}')
                with self.assertRaises(RuntimeError): module.write_state(run)
                (run/'filtered_articles.json').write_text(json.dumps(current_contract()))
                module.write_state(run)
                self.assertTrue(module.is_completed(run))
                with patch.object(module, 'current_contract', return_value={**current_contract(), 'profile_version':'next'}):
                    self.assertFalse(module.is_completed(run))

    def test_stored_label_survives_archive_removal_but_not_rule_upgrade(self):
        row = {'id': 1, 'url': 'https://mp.weixin.qq.com/s/test', 'label': keep_payload()}
        with tempfile.TemporaryDirectory() as tmp:
            require_current_selection([row], Path(tmp))
            row['label']['profile_version'] = 'old'
            with self.assertRaises(RuntimeError): require_current_selection([row], Path(tmp))

    def test_deadline_dispatch_blocks_unverified_record_before_send(self):
        import sqlite3
        from src.storage.db import init_database
        from src.reminders.cli import dispatch
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp)/'test.sqlite3'; init_database(db)
            with sqlite3.connect(db) as connection:
                connection.execute("INSERT INTO crawl_runs(run_id,status,created_at) VALUES('r','imported',1)")
                connection.execute("INSERT INTO articles(url,title,publish_time,application_type,deadline_at,crawl_run,created_at,updated_at) VALUES('test','test',1,'科研项目申请',1000,'r',1,1)")
                connection.execute("INSERT INTO deadline_reminders(article_id,deadline_at,remind_at,reminder_days,status) VALUES(1,1000,1,7,'pending')")
            with patch('src.reminders.cli.send_deadline_reminders') as send:
                result = dispatch(db, now=100)
                self.assertEqual(result['sent'], 0)
                self.assertEqual(result['skipped_geography'], 1)
                send.assert_not_called()
