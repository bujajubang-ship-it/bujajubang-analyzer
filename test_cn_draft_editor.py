import base64
import io
import json
import re
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from PIL import Image
from fastapi.testclient import TestClient
import httpx
import main

sys.path.insert(0, str(Path(__file__).parent / 'cnmaker_engine'))
import draft_editor as D


def picture(color='white'):
    out = io.BytesIO()
    Image.new('RGB', (64, 96), color).save(out, 'JPEG')
    return out.getvalue()


FORM = {'brand': '부자주방', 'product_name': '테스트 냄비', 'option': '흰색', 'size': '', 'mood': '밝은 주방',
        'sellpoints': [{'title': '손잡이', 'desc': '양쪽 손잡이'}, {'title': '뚜껑', 'desc': '투명 뚜껑'}, {'title': '보관', 'desc': '간단한 형태'}]}


class DraftEditorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.tasks = []
        tasks = self.tasks
        class Thread:
            def __init__(self, target, args=(), **kwargs): self.target, self.args = target, args
            def start(self): tasks.append((self.target, self.args))
        self.patches = [patch.object(D.G, 'log'), patch.object(D, 'ROOT', Path(self.temp.name)), patch.object(D, 'ACTIVE', set()),
                        patch.object(D.threading, 'Thread', Thread), patch.object(D.time, 'sleep'),
                        patch.object(D.G.P, '_claude', side_effect=self.analysis),
                        patch.object(D.G, '_oai_image', return_value=picture())]
        self.mocks = [p.start() for p in self.patches]
        self.addCleanup(lambda: [p.stop() for p in reversed(self.patches)])
        self.generate = self.mocks[-1]

    @staticmethod
    def analysis(content, *args):
        if any('[상품이미지 검증]' in row.get('text','') for row in content):
            return json.dumps(dict(structure_ok=True,color_ok=True,framing_ok=True,clean_ok=True,reason=''))
        if any('[구간별 문구 변주]' in row.get('text','') for row in content):
            return json.dumps({'hero':'가볍게 편안하게','details':[{'title':'확장 제목 '+str(i),'desc':'사용 장면을 자세하게 풀어 쓴 설명 '+str(i)} for i in range(3)]})
        identifiers = [re.search(r'id=([^;]+);', row.get('text', '')) for row in content]
        identifiers = [match.group(1) for match in identifiers if match]
        if identifiers:
            return json.dumps({'photos': [dict(id=ident, usable=True, description='같은 제품의 다른 구도',
                role='product', view=ident, colors=['흰색'], sections=[0,7,8]) for ident in identifiers]})
        return json.dumps(FORM)

    def create(self, count=1, finish=True):
        jid = D.create({'images': [base64.b64encode(picture()).decode()] * count})['id']
        if finish: self.run_task()
        return jid

    def run_task(self):
        target, args = self.tasks.pop(0)
        target(*args)

    def test_initial_draft_and_thumbnail_use_only_low_generation(self):
        jid = self.create(10)
        self.assertEqual(self.generate.call_count, 11)
        self.assertEqual({call.kwargs['quality'] for call in self.generate.call_args_list}, {'low'})
        doc = D.status(jid)
        self.assertEqual(doc['status'], 'done')
        self.assertEqual(len(doc['sections']), 11)
        self.assertTrue(all(s['low'] and not s['high'] for s in doc['sections']))
        self.assertEqual(Image.open(io.BytesIO(D.image_bytes(jid, 0, 'low'))).width, 430)
        # All ten uploaded photos are attached to the product analysis.
        photo_calls = [call for call in self.mocks[-2].call_args_list if '분석 대상 id=' in str(call)]
        self.assertEqual(len(photo_calls), 1)
        self.assertEqual(sum(row['type']=='image' for row in photo_calls[0].args[0]), 11)  # ten views + identity anchor

    def test_only_selected_sections_use_high_and_existing_drafts_survive(self):
        jid = self.create()
        before = D.status(jid)
        self.generate.reset_mock()
        D.action(jid, {'action': 'high', 'indices': [1, 4]})
        self.run_task()
        after = D.status(jid)
        self.assertEqual(self.generate.call_count, 2)
        self.assertEqual([s['index'] for s in after['sections'] if s['high']], [1, 4])
        self.assertEqual([s['low'] for s in before['sections']], [s['low'] for s in after['sections']])
        self.assertEqual(Image.open(io.BytesIO(D.image_bytes(jid, 1, 'high'))).width, 860)
        self.assertEqual({call.kwargs['quality'] for call in self.generate.call_args_list}, {'high'})

    def test_partial_edit_references_current_image_and_invalidates_old_high(self):
        jid = self.create()
        D.action(jid, {'action': 'high', 'indices': [0]}); self.run_task()
        prior = D.status(jid)
        raw = D.image_bytes(jid, 0, 'high')
        D.action(jid, {'action': 'edit', 'indices': [0], 'instruction': '배경만 변경'}); self.run_task()
        call = self.generate.call_args
        self.assertIn('배경만 변경', call.args[0])
        self.assertEqual(base64.b64decode(call.kwargs['ref_imgs_b64'][0][1]), raw)
        updated = D.status(jid)
        self.assertEqual(updated['sections'][0]['high'], '')
        self.assertEqual(updated['sections'][1]['low'], prior['sections'][1]['low'])

    def test_failed_high_retry_stays_high_and_preserves_previous_image(self):
        jid = self.create()
        previous = D.status(jid)['sections'][2]['low']
        self.generate.side_effect = RuntimeError('OpenAI 결제 한도 초과')
        D.action(jid, {'action': 'high', 'indices': [2]}); self.run_task()
        self.assertEqual(D.status(jid)['status'], 'partial')
        self.assertEqual(D.status(jid)['sections'][2]['low'], previous)
        self.generate.side_effect = None
        self.generate.reset_mock()
        D.action(jid, {'action': 'retry'}); self.run_task()
        self.assertEqual(self.generate.call_count, 1)
        self.assertEqual(self.generate.call_args.kwargs['quality'], 'high')

    def test_failed_edit_retry_keeps_instruction(self):
        jid = self.create()
        self.generate.side_effect = RuntimeError('요청 오류')
        D.action(jid, {'action': 'edit', 'indices': [0], 'instruction': '손잡이 그대로 배경만 변경'}); self.run_task()
        self.generate.side_effect = None
        D.action(jid, {'action': 'retry'}); self.run_task()
        self.assertIn('손잡이 그대로 배경만 변경', self.generate.call_args.args[0])

    def test_temporary_failure_retries_once_then_continues_other_sections(self):
        self.generate.side_effect = [RuntimeError('OpenAI 오류 503'), RuntimeError('OpenAI 오류 503')] + [picture()] * 10
        jid = self.create()
        doc = D.status(jid)
        self.assertEqual(self.generate.call_count, 12)
        self.assertEqual(doc['sections'][0]['status'], 'error')
        self.assertEqual(sum(bool(s['low']) for s in doc['sections']), 10)
        with self.assertRaises(D.DraftError): D.download(jid, 'low')

    def test_duplicate_action_is_rejected_before_charging(self):
        jid = self.create()
        D.action(jid, {'action': 'high', 'indices': [0]})
        with self.assertRaises(D.DraftError) as error:
            D.action(jid, {'action': 'high', 'indices': [0]})
        self.assertEqual(error.exception.status, 409)
        self.assertEqual(len(self.tasks), 1)

    def test_restart_retains_images_and_marks_interrupted_work_retryable(self):
        jid = self.create()
        prior = D.status(jid)['sections'][1]['low']
        D.action(jid, {'action': 'high', 'indices': [0]})
        D.ACTIVE.clear()
        doc = D.status(jid)
        self.assertEqual(doc['sections'][0]['status'], 'error')
        self.assertEqual(doc['sections'][0]['failed_action'], 'high')
        self.assertEqual(doc['sections'][1]['low'], prior)
        self.assertEqual(D.history()['items'][0]['id'], jid)

    def test_plan_save_does_not_generate_or_overwrite_images(self):
        jid = self.create()
        previous = D.status(jid)['sections']
        self.generate.reset_mock()
        D.action(jid, {'action': 'plan', 'form': {'product_name': '새 상품명'}})
        self.assertEqual(D.status(jid)['title'], '새 상품명')
        self.assertEqual(D.status(jid)['sections'], previous)
        self.generate.assert_not_called()

    def test_download_requires_complete_high_and_zip_only_selected(self):
        import zipfile
        jid = self.create()
        with self.assertRaises(D.DraftError): D.download(jid, 'high')
        D.action(jid, {'action': 'high', 'indices': [2, 5]}); self.run_task()
        raw, mime = D.download(jid, 'zip', [5])
        self.assertEqual(mime, 'application/zip')
        self.assertEqual(zipfile.ZipFile(io.BytesIO(raw)).namelist(), ['06.jpg'])

    def test_rejects_eleven_images_bad_indices_and_path_traversal(self):
        with self.assertRaises(D.DraftError): self.create(11)
        self.assertEqual(self.generate.call_count, 0)
        with self.assertRaises(D.DraftError): D.status('../../cn.env')
        jid = self.create()
        for index in (-1, 11, True, '0'):
            with self.assertRaises(D.DraftError): D.action(jid, {'action': 'high', 'indices': [index]})


class DraftProxyTests(unittest.TestCase):
    def setUp(self): self.client = TestClient(main.app)

    def test_all_draft_routes_require_login(self):
        with patch('main._site_auth', return_value=False):
            for url in ('', '/image?id=abc', '/download?id=abc', '/action?id=abc'):
                self.assertEqual(self.client.get('/cnmaker/api/drafts'+url).status_code, 401)
            self.assertEqual(self.client.post('/cnmaker/api/drafts', json={}).status_code, 401)

    def test_proxy_preserves_upstream_conflict(self):
        from cn_draft_api import create_router
        from fastapi import FastAPI
        app = FastAPI(); app.include_router(create_router(lambda _: True, 'http://worker', 'test-secret', lambda image: image))
        worker = AsyncMock(); worker.request.return_value = httpx.Response(409, json={'error':'현재 작업 중'})
        with patch('cn_draft_api.httpx.AsyncClient') as factory:
            factory.return_value.__aenter__.return_value = worker
            response = TestClient(app).post('/cnmaker/api/drafts/action?id=abc', json={'action':'high','indices':[0]})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(worker.request.call_args.kwargs['headers']['x-secret'], 'test-secret')


if __name__ == '__main__': unittest.main()
