import asyncio
import io
import json
import tempfile
import types
import unittest
from unittest.mock import patch

import test_cn_draft_editor as B
import text_ai as AI
import prompt_v3 as V
import page_maker as M


class GptAnalysisTests(unittest.TestCase):
    def test_vision_payload_preserves_images_and_order(self):
        body = AI.payload([{'type':'text','text':'상품'}, {'type':'image','source':{
            'type':'base64','media_type':'image/png','data':'YWJj'}}], 50)
        self.assertEqual(body['model'], 'gpt-6-astra')
        self.assertEqual(body['reasoning'], {'effort':'low'})
        self.assertGreater(body['max_output_tokens'], 50)
        self.assertFalse(body['store'])
        self.assertNotIn('temperature', body)
        self.assertEqual(body['input'][0]['content'], [
            {'type':'input_text','text':'상품'},
            {'type':'input_image','image_url':'data:image/png;base64,YWJj','detail':'high'}])

    def test_reasoning_is_not_returned_as_copy(self):
        data = {'status':'completed','output':[{'type':'reasoning','summary':[]},
            {'type':'message','content':[{'type':'output_text','text':'완료'}]}]}
        self.assertEqual(AI.output_text(data),'완료')
        for bad in ({'status':'incomplete','output':data['output']},
                    {'status':'completed','output':[]},
                    {'status':'completed','output':[{'type':'message','content':[{'type':'refusal'}]}]}):
            with self.assertRaises(RuntimeError): AI.output_text(bad)

    def test_all_legacy_analysis_entrypoints_use_gpt(self):
        with patch.object(M.AI,'complete',return_value='{"headline":"test"}') as call:
            with patch.object(AI, 'complete', return_value='ok') as pipeline_call:
                self.assertEqual(B.D.G.P._claude('analysis',500), 'ok')
                pipeline_call.assert_called_once_with('analysis',500)
            asyncio.run(M._claude_vision_json('vision',[]))
            result=asyncio.run(M.generate_product_copy('copy'))
            self.assertEqual(result['headline'],'test')
            self.assertEqual(call.call_count,2)

    def test_api_uses_responses_and_openai_key(self):
        result={'status':'completed','output':[{'type':'message','content':[{'type':'output_text','text':'ok'}]}]}
        with patch.object(AI,'_setting',return_value='test-key'), patch.object(AI.urllib.request,'urlopen',return_value=io.BytesIO(json.dumps(result).encode())) as send:
            self.assertEqual(AI.complete('test'),'ok')
            request=send.call_args.args[0]
            self.assertEqual(request.full_url,'https://api.openai.com/v1/responses')
            self.assertEqual(request.get_header('Authorization'),'Bearer test-key')

    def test_model_change_cannot_reuse_prior_analysis(self):
        with tempfile.TemporaryDirectory() as directory:
            api=types.SimpleNamespace(ANALYSIS_CACHE_ID='old',_claude=lambda *a:'{"a":1}')
            first=V.request_json(api,[],100,lambda x:None,directory,'test',lambda x:None)
            api.ANALYSIS_CACHE_ID=AI.CACHE_ID
            api._claude=lambda *a:'{"a":2}'
            second=V.request_json(api,[],100,lambda x:None,directory,'test',lambda x:None)
            self.assertEqual((first['a'],second['a']),(1,2))


if __name__=='__main__': unittest.main()
