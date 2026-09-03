import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import main
from page_maker import fetch_coupang_reference


def sample_plan():
    return {"sections": [{"number": number} for number in range(1, 12)]}


class CnmakerPlanStorageTest(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(main.app)

    @patch.object(main, "_site_auth", return_value=True)
    def test_rejects_invalid_project_id(self, *_):
        response = self.client.put("/cnmaker/api/plans/not-valid", json={"plan": sample_plan()})
        self.assertEqual(response.status_code, 400)

    @patch.object(main, "_site_auth", return_value=True)
    def test_saves_and_loads_plan(self, *_):
        with tempfile.TemporaryDirectory() as directory:
            plan_file = Path(directory) / "plans.json"
            with patch.object(main, "CN_PLANS_FILE", plan_file), \
                 patch.object(main, "_kv_restore", return_value=None), \
                 patch.object(main, "_kv_backup_checked", return_value={"ok": True, "error": None}):
                project_id = "abcdef123456"
                saved = self.client.put(
                    f"/cnmaker/api/plans/{project_id}", json={"plan": sample_plan()}
                )
                loaded = self.client.get(f"/cnmaker/api/plans/{project_id}")
        self.assertEqual(saved.status_code, 200)
        self.assertEqual(loaded.status_code, 200)
        self.assertEqual(len(loaded.json()["item"]["plan"]["sections"]), 11)


class CoupangReferenceTest(unittest.IsolatedAsyncioTestCase):
    async def test_rejects_non_coupang_url(self):
        with self.assertRaises(ValueError):
            await fetch_coupang_reference("https://example.com/product/1")


if __name__ == "__main__":
    unittest.main()
