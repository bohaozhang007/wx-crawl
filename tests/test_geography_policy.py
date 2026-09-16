import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.labeling.eligibility import geography_eligible
from src.labeling.schema import validate_payload
from src.labeling.runner import validate_model_label, ArticleInput
from src.labeling.importance_alerts import notify_high_importance
from src.reminders.cli import dispatch, reconcile_records
from src.storage.db import init_database
from tests.test_labeling_runner import keep_payload


class GeographyPolicyTest(unittest.TestCase):
    def test_allowed_scopes_and_legacy(self):
        for region in ['national','beijing','zhejiang']:
            label=keep_payload();label['geography']={'status':'eligible','regions':[region],'evidence':'原文依据'}
            self.assertTrue(geography_eligible(label))
            self.assertEqual(validate_payload(label),[])
        for geo in [None,{}, {'status':'out_of_scope','regions':['other'],'evidence':'江西'}, {'status':'unclear','regions':['unknown'],'evidence':''}]:
            label=keep_payload();label['geography']=geo
            self.assertFalse(geography_eligible(label))
            self.assertTrue(validate_payload(label))

    def test_geography_evidence_cannot_be_invented(self):
        label=keep_payload();label['geography']['evidence']='面向全国申报'
        article=ArticleInput(Path('/tmp/a'),{},'请于8月30日前提交申报材料。研发多模态大模型训练方法。')
        self.assertIn('geography.evidence is not present verbatim in the article',validate_model_label(label,article))

    def test_missing_geography_node_cannot_keep(self):
        label=keep_payload();label['decision_path'].remove('G1:PASS')
        self.assertTrue(any('G1' in error for error in validate_payload(label)))

    def test_no_high_alert_for_other_region_or_unclassified(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp);label=keep_payload();label['importance']['level']='high'
            label['deadline']={'status':'missing','timestamp':None,'raw_text':'','timezone':'Asia/Shanghai'}
            with patch('src.labeling.importance_alerts._config',return_value={'enabled':True,'mention_user_ids':['test']}),patch('src.labeling.importance_alerts.send_high_importance_alert') as send:
                for geo in [{}, {'status':'out_of_scope','regions':['other'],'evidence':'江西'}]:
                    label['geography']=geo;(p/'label.json').write_text(json.dumps(label))
                    result=notify_high_importance([p]);self.assertEqual(result['skipped_geography'],1)
                send.assert_not_called()

    def test_already_scheduled_jiangxi_and_legacy_are_cancelled_before_send(self):
        with tempfile.TemporaryDirectory() as tmp:
            db=Path(tmp)/'db.sqlite3';init_database(db)
            with sqlite3.connect(db) as c:
                c.execute("INSERT INTO crawl_runs(run_id,status,created_at) VALUES('r','imported',1)")
                for label in [{}, {'geography':{'status':'out_of_scope','regions':['other'],'evidence':'江西'}}]:
                    i=c.execute("INSERT INTO articles(url,title,publish_time,application_type,deadline_status,deadline_at,crawl_run,created_at,updated_at,label_json) VALUES(?, '江西指南',1,'科研指南申请','confirmed',1000,'r',1,1,?)",(str(len(label)),json.dumps(label))).lastrowid
                    c.execute("INSERT INTO deadline_reminders(article_id,deadline_at,remind_at,reminder_days,status) VALUES(?,1000,1,14,'pending')",(i,))
            with patch('src.reminders.cli.send_deadline_reminders') as send:
                result=dispatch(db,now=100)
                self.assertEqual(result['skipped_geography'],2);self.assertEqual(result['sent'],0);send.assert_not_called()
            result=reconcile_records(db,now=100,config={'enabled':True,'days_before':14,'max_attempts':3})
            self.assertEqual(result['created'],0)
            with sqlite3.connect(db) as c:self.assertEqual(c.execute("SELECT COUNT(*) FROM deadline_reminders WHERE status='cancelled'").fetchone()[0],2)
