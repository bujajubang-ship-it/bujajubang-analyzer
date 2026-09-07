import base64
import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch
import test_cn_draft_editor as B
import selection_flow as F
import mobile_layout as M
from PIL import Image
D=B.D


class SelectionTests(unittest.TestCase):
    def setUp(self):
        B.DraftEditorTests.setUp(self)
        class Pool:
            def __init__(self,**kwargs):pass
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def map(self,fn,items):return map(fn,items)
        patcher=patch.object(F,'ThreadPoolExecutor',Pool);patcher.start();self.addCleanup(patcher.stop)
    analysis=staticmethod(B.DraftEditorTests.analysis)
    run_task=B.DraftEditorTests.run_task

    def collect(self):
        jid=D.create({'workflow_version':4,'title':'그대로  상품명','images':[base64.b64encode(B.picture()).decode()]*2,
            'reference_images':[base64.b64encode(B.picture('pink')).decode()]})['id']
        self.run_task();return jid

    def review(self):
        jid=self.collect()
        D.action(jid,{'action':'select_photos','asset_ids':['input-0','reference-0']});self.run_task();return jid

    def test_collection_stops_before_analysis_and_selection_limits_evidence(self):
        jid=self.collect();self.mocks[-2].assert_not_called();self.generate.assert_not_called()
        self.assertEqual(D.status(jid)['status'],'selecting')
        D.action(jid,{'action':'select_photos','asset_ids':['input-0','reference-0']});self.run_task()
        self.assertEqual(D.status(jid)['status'],'reviewing',D.status(jid).get('error'))
        self.generate.assert_not_called()
        for call in self.mocks[-2].call_args_list:
            text=json.dumps(call.args[0],ensure_ascii=False)
            self.assertNotIn('id=input-1;',text);self.assertNotIn('id=reference-0;',text)

    def test_mobile_has_nine_detail_sections_and_square_thumbnail(self):
        jid=self.review();D.action(jid,{'action':'generate_all'});self.run_task()
        doc=D.status(jid);self.assertEqual(doc['status'],'done',doc.get('error'))
        self.assertEqual(len(doc['sections']),10)
        self.assertEqual(doc['sections'][8]['title'],'PRODUCT INFO')
        self.assertEqual(self.generate.call_count,7)
        self.assertEqual(self.generate.call_args.kwargs['size'],'1024x1024')
        self.assertIn('상품명·한글·영문·숫자·아이콘·문구 모두 없음',self.generate.call_args_list[1].args[0])
        raw,_=D.download(jid,'low');im=Image.open(io.BytesIO(raw))
        expected=sum(Image.open(io.BytesIO(D.image_bytes(jid,i,'low'))).height for i in range(9))
        self.assertEqual(im.height,expected)

    def test_selected_info_is_analyzed_but_never_used_as_product_reference(self):
        jid=self.collect()
        D.action(jid,{'action':'select_photos','asset_ids':['input-0','input-1'],'uses':{'input-1':'info'}});self.run_task()
        doc=D.read(jid)
        self.assertEqual(doc['assets'][1]['use_as'],'info')
        self.assertEqual([a['id'] for a in D.S.choose(doc,0)],['input-0'])

    def test_unknown_id_or_only_reference_cannot_start_analysis(self):
        jid=self.collect()
        for ids in (['unknown'],['reference-0'],[]):
            with self.assertRaises(D.DraftError):D.action(jid,{'action':'select_photos','asset_ids':ids})
        self.mocks[-2].assert_not_called()

    def test_three_analysis_failures_publish_popup_id_and_resume(self):
        jid=self.collect();self.mocks[-2].side_effect=lambda *a:'{'
        D.action(jid,{'action':'select_photos','asset_ids':['input-0']});self.run_task()
        self.assertEqual(self.mocks[-2].call_count,3)
        doc=D.status(jid);self.assertEqual(doc['status'],'error');self.assertTrue(doc['failure_id'])
        self.assertTrue(doc['assets'][0]['selected']);self.generate.assert_not_called()
        self.mocks[-2].side_effect=self.analysis
        D.action(jid,{'action':'retry'});self.run_task();self.assertEqual(D.status(jid)['status'],'reviewing')

    def test_copy_crop_and_primary_photo_save_without_generation(self):
        jid=self.review();form=dict(D.read(jid)['form']);form['sellpoints'][1]['desc']='직접 수정한 문구'
        form.update(section_photos={'4':'input-0'},translations={'input-0':'수정 번역'},crops={'input-0':[0,.2,1,.8]})
        D.action(jid,{'action':'plan','form':form});doc=D.status(jid)
        self.assertEqual(doc['form']['sellpoints'][1]['desc'],'직접 수정한 문구')
        self.assertEqual(doc['translations']['input-0'],'수정 번역')
        self.assertEqual(doc['assets'][0]['crop'],[0,.2,1,.8]);self.generate.assert_not_called()

    def test_original_detail_pixels_do_not_call_image_api_without_color_change(self):
        jid=self.review();doc=D.read(jid);a=doc['assets'][0]
        image=M.product_photo(doc,a,D.folder(jid),D.G,'low')
        original=Image.open(D.folder(jid)/a['file']).convert('RGB')
        self.assertEqual(image.tobytes(),original.tobytes());self.generate.assert_not_called()

    def test_selection_cannot_be_bypassed_by_regenerate(self):
        jid=self.collect()
        with self.assertRaises(D.DraftError):D.action(jid,{'action':'regenerate','indices':[0]})
        self.generate.assert_not_called()


if __name__=='__main__':unittest.main()
