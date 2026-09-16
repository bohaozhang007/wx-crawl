import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.labeling.eligibility import deadline_expired
from src.labeling.importance_alerts import notify_high_importance
from tests.test_labeling_runner import keep_payload


class DeadlineEligibilityTest(unittest.TestCase):
    def test_boundary_and_unknown(self):
        for stamp, expected in [(99, True), (100, True), (101, False)]:
            self.assertEqual(deadline_expired({'deadline': {'status': 'confirmed', 'timestamp': stamp}}, now=100), expected)
        for deadline in [{}, {'status': 'missing', 'timestamp': None}, {'status': 'ambiguous', 'timestamp': None}]:
            self.assertFalse(deadline_expired({'deadline': deadline}, now=100))

    def test_alert_expired_blocked_unknown_allowed(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp);label=keep_payload();label['importance']['level']='high'
            label['deadline']={'status':'confirmed','timestamp':100,'raw_text':'截止日期','timezone':'Asia/Shanghai'}
            (p/'label.json').write_text(json.dumps(label))
            with patch('src.labeling.importance_alerts._config', return_value={'enabled':True,'mention_user_ids':['test']}), patch('src.labeling.importance_alerts.send_high_importance_alert', return_value='ok') as send:
                result=notify_high_importance([p])
                self.assertEqual(result['skipped_expired'],1);send.assert_not_called()
                label['deadline']={'status':'ambiguous','timestamp':None,'raw_text':'另行通知','timezone':'Asia/Shanghai'}
                (p/'label.json').write_text(json.dumps(label))
                result=notify_high_importance([p])
                self.assertEqual(result['sent'],1);send.assert_called_once()

    def test_selection_expired_blocked_unknown_allowed(self):
        path=Path(__file__).resolve().parents[1]/'skill/article-label-export/scripts/select_articles.py'
        spec=importlib.util.spec_from_file_location('deadline_selector',path)
        selector=importlib.util.module_from_spec(spec);spec.loader.exec_module(selector)
        label=keep_payload()
        candidate={'article_dir':'/tmp/account/article','title':'test','url':'https://mp.weixin.qq.com/s/test','publish_time':0}
        with patch.object(selector,'write_ledger'), patch.object(selector,'match_run_articles',return_value=([candidate],[])), patch.object(selector,'valid_label',return_value=(label,None)):
            label['deadline']={'status':'confirmed','timestamp':100,'raw_text':'截止日期','timezone':'Asia/Shanghai'}
            result=selector.select_matches(Path('/tmp/run'))
            self.assertEqual(len(result['articles']),0)
            label['deadline']={'status':'missing','timestamp':None,'raw_text':'','timezone':'Asia/Shanghai'}
            result=selector.select_matches(Path('/tmp/run'))
            self.assertEqual(len(result['articles']),1)
