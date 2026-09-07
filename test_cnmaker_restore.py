import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

import main


class CnmakerRestoreTest(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(main.app)

    def test_page_still_requires_login(self):
        with patch.object(main, '_site_auth', return_value=False):
            response = self.client.get('/cnmaker')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, Path('static/site_login.html').read_bytes())

    def test_old_screen_and_logo_settings_are_available(self):
        with patch.object(main, '_site_auth', return_value=True):
            response = self.client.get('/cnmaker')
        self.assertIn('/static/cnmaker-drafts.js', response.text)
        self.assertIn('/cnmaker/api/logo_config', response.text)
        self.assertNotIn('/cnmaker/api/plan', response.text)
        self.assertEqual(self.client.get('/cnmaker/api/logo_config').status_code, 200)

    def test_url_start_uses_original_worker_contract(self):
        worker = AsyncMock()
        import httpx
        worker.post.return_value = httpx.Response(200, json={'job': 'test-job'})
        with patch.object(main.httpx, 'AsyncClient') as factory:
            factory.return_value.__aenter__.return_value = worker
            response = self.client.post('/cnmaker/api/start', json={'url': 'https://example.com/product', 'category': 'kitchen'})
        self.assertEqual(response.json(), {'job': 'test-job'})
        self.assertTrue(worker.post.call_args.args[0].endswith('/cnmaker/start'))
        self.assertEqual(worker.post.call_args.kwargs['json']['category'], 'kitchen')

    def test_full_result_applies_logo_but_thumbnail_does_not(self):
        import httpx
        for query, expected_calls in (('job=test', 1), ('job=test&thumb=1', 0)):
            worker = AsyncMock()
            worker.get.return_value = httpx.Response(200, content=b'image')
            with patch.object(main.httpx, 'AsyncClient') as factory, patch.object(main, '_cn_apply_logo', return_value=b'logo-image') as logo:
                factory.return_value.__aenter__.return_value = worker
                response = self.client.get('/cnmaker/api/result?' + query)
            self.assertEqual(logo.call_count, expected_calls)
            self.assertEqual(response.content, b'logo-image' if expected_calls else b'image')


if __name__ == '__main__':
    unittest.main()
