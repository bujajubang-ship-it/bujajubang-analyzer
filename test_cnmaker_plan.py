import json
import os
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

import main


class FakeAIResponse:
    status_code = 200

    def json(self):
        plan = {
            "product": {"name": "테스트 상품"},
            "features": [],
            "palette": {"background": "아이보리", "secondary": "연베이지", "accent": "차콜"},
            "sections": [
                {
                    "number": number,
                    "type": f"구간 {number}",
                    "enabled": True,
                    "title": f"제목 {number}",
                    "body": "본문",
                    "image_prompt": "이미지 계획",
                }
                for number in range(1, 12)
            ],
            "warnings": [],
        }
        return {
            "output": [{
                "type": "message",
                "content": [{"type": "output_text", "text": json.dumps(plan, ensure_ascii=False)}],
            }]
        }


async def fake_ai_post(*args, **kwargs):
    assert "temperature" not in args[1]
    assert args[1]["model"] == "gpt-5.6-sol"
    assert args[1]["input"][0]["content"][0]["type"] == "input_text"
    return FakeAIResponse()


async def fake_collect_product(*args, **kwargs):
    return {"title": "수집된 테스트 상품", "images": []}


async def fake_collect_with_upload_fallback(url, allow_uploaded_fallback=False):
    if allow_uploaded_fallback:
        return {
            "title": "",
            "images": [],
            "warning": "1688 자동 수집이 차단되어 직접 올린 사진만 사용했습니다.",
        }
    raise ValueError("collection blocked")


class CnmakerPlanTest(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(main.app)

    def test_login_is_required(self):
        response = self.client.post("/cnmaker/api/plan", json={"url1688": "https://example.com/item"})
        self.assertEqual(response.status_code, 401)

    @patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"})
    @patch.object(main, "_site_auth", return_value=True)
    @patch.object(main, "_cn_collect_product", side_effect=fake_collect_product)
    @patch.object(main, "_cn_store_plan", return_value={"item": {}, "backup": {"ok": True}})
    @patch.object(main, "_openai_post", side_effect=fake_ai_post)
    def test_returns_eleven_section_plan(self, *_):
        response = self.client.post(
            "/cnmaker/api/plan",
            json={"url1688": "https://example.com/item", "product": {"name": "테스트 상품"}},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()["plan"]["sections"]), 11)
        self.assertEqual(len(response.json()["plan"]["features"]), 3)

    @patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"})
    @patch.object(main, "_site_auth", return_value=True)
    @patch.object(main, "_cn_collect_product", side_effect=fake_collect_product)
    def test_rejects_more_than_ten_images(self, *_):
        response = self.client.post(
            "/cnmaker/api/plan",
            json={"url1688": "https://example.com/item", "images": ["x"] * 11},
        )
        self.assertEqual(response.status_code, 400)


    @patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"})
    @patch.object(main, "_site_auth", return_value=True)
    @patch.object(main, "_cn_collect_product", side_effect=fake_collect_with_upload_fallback)
    @patch.object(main, "_cn_store_plan", return_value={"item": {}, "backup": {"ok": True}})
    @patch.object(main, "_openai_post", side_effect=fake_ai_post)
    def test_uploaded_image_continues_when_1688_collection_is_blocked(self, *_):
        response = self.client.post(
            "/cnmaker/api/plan",
            json={
                "url1688": "https://detail.1688.com/offer/998500353586.html",
                "images": ["data:image/png;base64,YQ=="],
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("자동 수집", response.json()["plan"]["warnings"][0])


if __name__ == "__main__":
    unittest.main()
