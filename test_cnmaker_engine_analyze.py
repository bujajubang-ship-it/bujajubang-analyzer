import pathlib
import sys
import io
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image


ENGINE_DIR = pathlib.Path(__file__).parent / "cnmaker_engine"
sys.path.insert(0, str(ENGINE_DIR))

import server  # noqa: E402


class CnmakerEngineAnalyzeTest(unittest.TestCase):
    @patch.object(server.pipeline, "detect_source", return_value="cninsider")
    @patch.object(
        server.gptmaker,
        "login_and_scrape",
        return_value={"title": "수집 상품", "main_imgs": ["https://example.com/1.jpg"]},
    )
    def test_collects_without_starting_image_generation(self, *_):
        result = server.analyze_product("https://www.cninsider.co.kr/mall/#/detail?id=1")
        self.assertEqual(result["title"], "수집 상품")
        self.assertEqual(result["images"], ["https://example.com/1.jpg"])

    def test_rejects_invalid_url(self):
        with self.assertRaises(ValueError):
            server.analyze_product("not-a-url")

    @patch.object(server.pipeline, "detect_source", return_value="other")
    @patch.object(
        server.gptmaker,
        "login_and_scrape",
        return_value={"title": "1688 상품", "main_imgs": ["https://example.com/1688.jpg"]},
    )
    def test_accepts_direct_1688_url(self, *_):
        result = server.analyze_product("https://detail.1688.com/offer/998500353586.html")
        self.assertEqual(result["title"], "1688 상품")

    def test_low_resolution_draft_uses_enabled_sections_only(self):
        buffer = io.BytesIO()
        Image.new("RGB", (1024, 1536), "white").save(buffer, "JPEG")
        generated = buffer.getvalue()
        plan = {
            "product": {"name": "테스트 상품"},
            "palette": {},
            "sections": [
                {"number": 1, "enabled": True, "image_prompt": "첫 장면"},
                {"number": 2, "enabled": False, "image_prompt": "제외 장면"},
            ],
        }
        with tempfile.TemporaryDirectory() as directory:
            reference = pathlib.Path(directory) / "reference.jpg"
            output = pathlib.Path(directory) / "draft.jpg"
            reference.write_bytes(generated)
            with patch.object(server.gptmaker, "_oai_image", return_value=generated) as image_api:
                result = server.gptmaker.run_plan_draft(plan, [str(reference)], [], str(output))
            with Image.open(output) as draft_image:
                size = draft_image.size
        self.assertEqual(result["section_count"], 1)
        self.assertEqual(image_api.call_count, 1)
        self.assertEqual(size, (430, 645))


if __name__ == "__main__":
    unittest.main()
