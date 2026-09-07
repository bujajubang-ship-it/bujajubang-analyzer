import base64
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch, AsyncMock

import test_cn_draft_editor as B
import prompt_v3 as V

D = B.D


class ResponseTests(unittest.TestCase):
    def test_fenced_json_and_explanation_preserve_braces_inside_strings(self):
        value = {'photos': [{'id': 'a', 'description': '문자 {그대로}', 'usable': True}]}
        self.assertEqual(V.parse_response('분석 결과입니다.\n```json\n' + json.dumps(value) + '\n```\n완료했습니다.'), value)

    def test_extra_objects_and_truncated_json_are_not_silently_accepted(self):
        for text in ('{"photos": []}\n{"photos": []}', '{"photos": [{"id":"a"}', '[{"photos": []}]'):
            with self.subTest(text=text), self.assertRaises(V.AnalysisFormatError):
                V.parse_response(text)

    def test_photo_ids_must_be_complete_unique_and_boolean(self):
        for rows in ([{'id':'a','usable':True}]*2, [{'id':'a','usable':'true'}], [{'id':'b','usable':True}]):
            with self.assertRaises(V.AnalysisFormatError): V.validate_photos({'photos':rows}, ['a'])

    def test_only_validated_result_is_cached_and_one_retry_is_bounded(self):
        with tempfile.TemporaryDirectory() as path:
            valid=json.dumps(B.FORM)
            api=Mock();api._claude.side_effect=[valid+'\n'+valid, valid]
            progress=Mock();content=[{'type':'text','text':'test'}]
            first=V.request_json(api,content,5500,V.validate_form,path,'기획',progress)
            self.assertEqual(api._claude.call_count,2)
            self.assertEqual(V.request_json(api,content,5500,V.validate_form,path,'기획',progress),first)
            self.assertEqual(api._claude.call_count,2)
            api._claude.side_effect=['{','{','{']
            with self.assertRaises(V.AnalysisFormatError):
                V.request_json(api,[{'type':'text','text':'changed'}],5500,V.validate_form,path,'기획',progress)
            self.assertEqual(len(list(Path(path).glob('analysis-v3-*.json'))),1)

    def test_all_user_prompt_blocks_are_loaded_and_values_are_not_reinterpreted(self):
        self.assertEqual(set(V.BLOCKS),set('ABCDFGHI')|{f'E-{i}' for i in range(11)})
        form=dict(B.FORM, product_name='  내 {규격}  상품  ')
        for index in range(11):
            prompt=V.image_prompt(form,index)
            self.assertIn('  내 {규격}  상품  ',prompt)
            self.assertNotIn('별점 4.9/5',prompt)
        self.assertIn('또 다른 실제 활용 장면',V.image_prompt(form,6))
        self.assertIn('SIZE 영역 자체를 만들지',V.image_prompt(form,8))
        self.assertIn('선명도와 세부 디테일만',V.image_prompt(form,0,'high',current=True))
        self.assertNotIn('상단 약 30~35%',V.image_prompt(form,0,'high',current=True))


class DraftV3Tests(unittest.TestCase):
    setUp = B.DraftEditorTests.setUp
    analysis = staticmethod(B.DraftEditorTests.analysis)
    create = B.DraftEditorTests.create
    run_task = B.DraftEditorTests.run_task

    def test_submitted_name_and_brand_are_enforced_for_generation(self):
        name='  내가 정한  상품명 {규격}  '
        jid=D.create({'title':name,'images':[base64.b64encode(B.picture()).decode()]})['id']
        self.run_task();doc=D.status(jid)
        self.assertEqual(doc['status'],'done',doc.get('error'))
        self.assertEqual(doc['title'],name)
        self.assertEqual(doc['form']['product_name'],name)
        self.assertEqual(doc['form']['brand'],'')
        self.assertEqual([s['title'] for s in doc['sections']],V.TITLES)
        self.assertTrue(all(name in c.args[0] for c in self.generate.call_args_list))
        self.assertIn('또 다른 실제 활용 장면',self.generate.call_args_list[6].args[0])

    def test_failed_planning_resumes_from_cached_photos_and_reports_failed_stage(self):
        normal=self.analysis
        def fail_plan(content,*args):
            result=normal(content,*args)
            return result if '분석 대상 id=' in str(content) else result+'\n'+result
        self.mocks[-2].side_effect=fail_plan
        jid=self.create();doc=D.status(jid)
        self.assertEqual(doc['status'],'error')
        self.assertIn('기획',doc['failed_stage'])
        self.assertTrue(doc['assets'])
        self.assertEqual(len(list(D.folder(jid).glob('analysis-v3-*.json'))),1)
        self.generate.assert_not_called()
        self.mocks[-2].reset_mock();self.mocks[-2].side_effect=normal
        with patch.object(D.S,'collect',side_effect=AssertionError('must reuse collection')):
            D.action(jid,{'action':'retry'});self.run_task()
        self.assertEqual(D.status(jid)['status'],'done')
        self.assertEqual(self.mocks[-2].call_count,1)
        self.assertEqual(self.generate.call_count,11)

    def test_progress_is_observable_before_each_batch_and_generation(self):
        seen=[];original=self.analysis
        def observe(content,*args):
            doc=json.loads(next(D.ROOT.glob('*/state.json')).read_text(encoding='utf-8'))
            seen.append((doc['message'],len(doc['assets'])))
            return original(content,*args)
        self.mocks[-2].side_effect=observe
        jid=self.create(10)
        self.assertIn('1~10/10',seen[0][0]);self.assertEqual(seen[0][1],10)
        self.assertIn('색상',seen[1][0])
        self.assertTrue(any('11/11' in e['message'] for e in D.status(jid)['progress_events']))

    def test_prompt_cache_is_not_publicly_downloadable(self):
        jid=self.create();cache=next(D.folder(jid).glob('analysis-v3-*.json'))
        with self.assertRaises(D.DraftError):D.source_bytes(jid,cache.stem)


class BrandlessDownloadTests(unittest.TestCase):
    def test_full_draft_jpg_is_not_rebranded_by_proxy(self):
        import httpx
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from cn_draft_api import create_router
        logo=Mock();app=FastAPI();app.include_router(create_router(lambda _:True,'http://worker','test',logo))
        worker=AsyncMock();worker.request.return_value=httpx.Response(200,content=b'original-jpg',headers={'content-type':'image/jpeg'})
        with patch('cn_draft_api.httpx.AsyncClient') as factory:
            factory.return_value.__aenter__.return_value=worker
            result=TestClient(app).get('/cnmaker/api/drafts/download?id=test&quality=low')
        self.assertEqual(result.content,b'original-jpg');logo.assert_not_called()


if __name__ == '__main__': unittest.main()
