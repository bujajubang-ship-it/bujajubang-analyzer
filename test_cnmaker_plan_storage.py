import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import main
def sample_plan():
    return {
        "features": [{"title": "체크 1"}, {"title": "체크 2"}, {"title": "체크 3"}],
        "sections": [{"number": number} for number in range(1, 12)],
    }


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

    @patch.object(main, "_site_auth", return_value=True)
    def test_confirm_creates_versions_and_edit_returns_to_draft(self, *_):
        with tempfile.TemporaryDirectory() as directory:
            plan_file = Path(directory) / "plans.json"
            with patch.object(main, "CN_PLANS_FILE", plan_file), \
                 patch.object(main, "_kv_restore", return_value=None), \
                 patch.object(main, "_kv_backup_checked", return_value={"ok": True, "error": None}):
                project_id = "123456abcdef"
                self.client.put(f"/cnmaker/api/plans/{project_id}", json={"plan": sample_plan()})
                first = self.client.post(f"/cnmaker/api/plans/{project_id}/confirm")
                second = self.client.post(f"/cnmaker/api/plans/{project_id}/confirm")
                self.client.put(f"/cnmaker/api/plans/{project_id}", json={"plan": sample_plan()})
                loaded = self.client.get(f"/cnmaker/api/plans/{project_id}").json()["item"]
        self.assertEqual(first.json()["version"], 1)
        self.assertEqual(second.json()["version"], 2)
        self.assertEqual(len(loaded["revisions"]), 2)
        self.assertEqual(loaded["status"], "draft")
        self.assertEqual(loaded["confirmed_plan"]["sections"], sample_plan()["sections"])

    @patch.object(main, "_site_auth", return_value=True)
    @patch.object(main, "_cn_load_plans", return_value={"abcdef123456": {"status": "draft"}})
    def test_draft_generation_requires_confirmed_plan(self, *_):
        response = self.client.post(
            "/cnmaker/api/plans/abcdef123456/generate-draft", json={"images": []}
        )
        self.assertEqual(response.status_code, 409)

    @patch.object(main, "_site_auth", return_value=True)
    def test_draft_complete_unlocks_full_editing(self, *_):
        with tempfile.TemporaryDirectory() as directory:
            plan_file = Path(directory) / "plans.json"
            plan_file.write_text(
                '{"abcdef123456":{"id":"abcdef123456","plan":{"sections":[]}}}',
                encoding="utf-8",
            )
            with patch.object(main, "CN_PLANS_FILE", plan_file), \
                 patch.object(main, "_kv_backup_checked", return_value={"ok": True, "error": None}):
                response = self.client.post(
                    "/cnmaker/api/plans/abcdef123456/draft-complete"
                )
                loaded = json.loads(plan_file.read_text(encoding="utf-8"))

        self.assertEqual(response.status_code, 200)
        self.assertTrue(loaded["abcdef123456"]["low_res_generated"])


    @patch.object(main, "_site_auth", return_value=True)
    def test_rejects_plan_without_three_checkpoints(self, *_):
        response = self.client.put(
            "/cnmaker/api/plans/abcdef123456",
            json={"plan": {"features": [{"title": "하나"}], "sections": [{"number": n} for n in range(1, 12)]}},
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("체크포인트 3개", response.json()["error"])


if __name__ == "__main__":
    unittest.main()
