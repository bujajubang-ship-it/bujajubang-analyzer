import io
import unittest
from unittest.mock import patch
import test_cn_selection as B
import merchandising as M
import mobile_layout as L
import prompt_v3 as V

D=B.D


class MerchandisingTests(B.SelectionTests):
    def test_point_sections_prefer_unused_photo_ids(self):
        jid=self.review();doc=D.read(jid)
        for i,a in enumerate(doc['assets']):
            a.update(id='p'+str(i),usable=True,selected=True,use_as='product',view='full')
        doc['sections'][3]['reference_ids']=['p0'];doc['sections'][4]['reference_ids']=['p1']
        self.assertEqual(D.S.choose(doc,5)[0]['id'],'p0')
        D.save(doc)
    def test_replan_keeps_selected_photos_and_does_not_generate_images(self):
        jid=self.review();doc=D.read(jid);selected={a['id'] for a in doc['assets'] if a.get('selected')}
        D.action(jid,{'action':'replan'});self.run_task();current=D.status(jid)
        self.assertEqual(current['status'],'reviewing',current.get('error'))
        self.assertEqual({a['id'] for a in current['assets'] if a.get('selected')},selected)
        self.generate.assert_not_called()
    def test_review_can_exclude_analyzed_photo_without_deleting_original(self):
        jid=self.collect();D.action(jid,{'action':'select_photos','asset_ids':['input-0','input-1']});self.run_task();doc=D.read(jid);asset=doc['assets'][0];original=asset['file']
        result=D.action(jid,{'action':'remove_asset','asset_id':asset['id']})
        current=D.read(jid)
        self.assertFalse(current['assets'][0]['selected'])
        self.assertTrue(current['assets'][0]['excluded_after_analysis'])
        self.assertTrue((D.folder(jid)/original).exists())
        self.assertNotIn(asset['id'],sum(current.get('photo_plan',{}).values(),[]))

    def test_review_cannot_remove_last_product_photo(self):
        jid=self.review();doc=D.read(jid)
        for asset in doc['assets'][1:]: asset['selected']=False
        D.save(doc)
        with self.assertRaises(D.DraftError):D.action(jid,{'action':'remove_asset','asset_id':doc['assets'][0]['id']})
    def test_edited_summary_invalidates_derived_copy_without_changing_summary(self):
        jid=self.review();doc=D.read(jid)
        original=[dict(p) for p in doc['form']['sellpoints']]
        first=M.section_copy(doc,D.G,D.folder(jid),lambda s:None)
        count=self.mocks[-2].call_count
        M.section_copy(doc,D.G,D.folder(jid),lambda s:None)
        self.assertEqual(self.mocks[-2].call_count,count)
        self.assertEqual(doc['form']['sellpoints'],original)
        doc['form']['sellpoints'][0]['desc']='새로 확정한 장점 설명'
        M.section_copy(doc,D.G,D.folder(jid),lambda s:None)
        self.assertEqual(self.mocks[-2].call_count,count+1)
        self.assertIn('새로 확정한 장점 설명',self.mocks[-2].call_args.args[0][0]['text'])
        with patch.object(L,'lines',wraps=L.lines) as draw:
            L.compose(doc,1,[doc['assets'][0]],D.folder(jid),'low',D.G)
        self.assertIn('새로 확정한 장점 설명',[c.args[0] for c in draw.call_args_list])
        with patch.object(L,'lines',wraps=L.lines) as draw:
            L.compose(doc,3,[doc['assets'][0]],D.folder(jid),'low',D.G)
        self.assertIn(first['details'][0]['desc'],[c.args[0] for c in draw.call_args_list])
        self.assertNotIn('새로 확정한 장점 설명',[c.args[0] for c in draw.call_args_list])

    def test_specs_are_editable_and_empty_values_stay_empty(self):
        jid=self.review();form=dict(D.read(jid)['form'])
        form['product_info']=[{'label':'제조국','value':''},{'label':'소재','value':'면 80%'}]
        D.action(jid,{'action':'plan','form':form})
        self.assertEqual(D.status(jid)['form']['product_info'],form['product_info'])
        form['product_info']=[];D.action(jid,{'action':'plan','form':form})
        self.assertEqual(D.status(jid)['form']['product_info'],[])

    def test_overlay_cleanup_is_cached_by_quality(self):
        jid=self.review();doc=D.read(jid);asset=doc['assets'][0];asset['original_text']='缓震防滑'
        L.product_photo(doc,asset,D.folder(jid),D.G,'low')
        self.assertIn('모두 지우고',self.generate.call_args.args[0])
        self.assertIn('비율·봉제선',self.generate.call_args.args[0])
        L.product_photo(doc,asset,D.folder(jid),D.G,'low');self.assertEqual(self.generate.call_count,1)
        L.product_photo(doc,asset,D.folder(jid),D.G,'high');self.assertEqual(self.generate.call_count,2)

    def test_info_uses_cutout_and_renders_only_confirmed_rows(self):
        jid=self.review();doc=D.read(jid)
        doc['form']['product_info']=[{'label':'소재','value':'면'},{'label':'제조국','value':''}]
        with patch.object(L,'lines',wraps=L.lines) as wrap:
            raw=L.product_info(doc,[doc['assets'][0]],D.folder(jid),'low',D.G)
        self.assertTrue(raw.startswith(b'\xff\xd8'))
        self.assertIn('사람·다리·손·신발',self.generate.call_args.args[0])
        seen=[c.args[0] for c in wrap.call_args_list]
        self.assertIn('소재',seen);self.assertNotIn('제조국',seen)

    def test_automatic_point_leads_do_not_repeat_anchor(self):
        jid=self.review();doc=D.read(jid)
        base=doc['assets'][0]
        doc['assets']=[dict(base,id=str(i),description='구조 디테일',view=str(i)) for i in range(3)]
        doc['photo_plan']={'3':['1'],'4':['2'],'5':['0']}
        self.assertEqual([D.S.choose(doc,i)[0]['id'] for i in (3,4,5)],['1','2','0'])


class SpecificationTests(unittest.TestCase):
    def test_category_fields_and_marketing_prompt(self):
        rows=M.recommend({'product_name':'식기 접시'})
        self.assertIn('식기세척기 사용',[r['label'] for r in rows])
        self.assertTrue(all(not r['value'] for r in rows if r['label']!='상품명'))
        prompt=V.planning('양말',{'workflow_version':4},[{'translation':'폭신한 바닥'}])
        self.assertIn('폭신한 바닥',prompt);self.assertIn('적극적으로 확장',prompt)
        self.assertIn('제조국',prompt);self.assertIn('photo_plan',prompt)
