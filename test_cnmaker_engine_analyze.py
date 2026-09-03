import pathlib
import sys
import unittest
from unittest.mock import patch


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


if __name__ == "__main__":
    unittest.main()
