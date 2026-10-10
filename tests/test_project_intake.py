import unittest
from src.labeling.project_intake import normalize, table_fields


class IntakeTests(unittest.TestCase):
    def extract(self, field, value, quote):
        name='2026年探索者基金'
        raw={'version':1,'project_name':{'value':name,'evidence':name},field:{'value':value,'evidence':quote}}
        return normalize(raw,name+'。'+quote)

    def test_confirmed_values_and_normalization(self):
        for value,quote in [('25','每项课题资助人民币25万元'),('25','每项课题资助人民币250000元')]:
            with self.subTest(quote=quote):self.assertEqual(self.extract('amount_wan',value,quote)['amount_wan']['value'],'25')
        for field,quote in [('start_date','申报开始日期为2026年10月9日'),('end_date','申报截止2026年10月9日')]:
            self.assertEqual(self.extract(field,'2026-10-09',quote)[field]['value'],'2026-10-09')

    def test_uncertain_amounts_never_become_fixed_amount(self):
        for quote in ['每项最高25万元','每项课题资助20至25万元','每项课题约25万元','总预算25万元','每项课题资助25万美元','往年每项25万元','每项需配套25万元']:
            with self.subTest(quote=quote):self.assertNotIn('amount_wan',self.extract('amount_wan','25',quote))
        self.assertNotIn('amount_wan',self.extract('amount_wan','0','每项资助0万元'))
        self.assertNotIn('amount_wan',self.extract('amount_wan','100','每项资助25万元'))

    def test_missing_year_publication_date_and_guess_rejected(self):
        for quote in ['10月9日截止','预计2026年10月9日截止','发布日期2026年10月9日']:
            self.assertNotIn('end_date',self.extract('end_date','2026-10-09',quote))
        self.assertNotIn('authority',self.extract('authority','科技部','由科技厅组织'))
        self.assertEqual(normalize({'version':1,'authority':{'value':'科技厅','evidence':'科技厅'}},'科技厅'),{'version':1})

    def test_no_title_fallback_or_empty_field_writes(self):
        self.assertEqual(table_fields({},'倒计时'),{})
        self.assertEqual(table_fields({'version':1,'project_name':{'value':'倒计时1天','evidence':'倒计时1天'}},'倒计时1天'),{})
        self.assertEqual(table_fields({'version':1,'project_name':{'value':None,'evidence':''}},''),{})

    def test_no_category_urgency_or_actor_inference(self):
        clean=self.extract('authority','科技厅','主管部门为科技厅')
        fields=table_fields(clean,'2026年探索者基金。主管部门为科技厅')
        self.assertEqual(set(fields),{'正式项目名称','主管部门'})


    def test_sponsor_or_signature_is_not_supervising_authority(self):
        for quote in ['发榜单位：阿里云','赛事主办方、阿里云负责人介绍','由阿里云共同发起','阿里云2026年10月8日']:
            self.assertNotIn('authority',self.extract('authority','阿里云',quote))
        self.assertIn('authority',self.extract('authority','科技厅','科技厅牵头组织申报'))
