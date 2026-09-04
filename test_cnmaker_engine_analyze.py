import pathlib
import sys
import io
import json
import time
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image


ENGINE_DIR = pathlib.Path(__file__).parent / "cnmaker_engine"
sys.path.insert(0, str(ENGINE_DIR))

import server  # noqa: E402


class CnmakerEngineAnalyzeTest(unittest.TestCase):
    def test_text_safe_layout_keeps_hero_photo_full_bleed(self):
        image = Image.new("RGB", (860, 1290), "white")
        for px in range(100, 760):
            for py in range(100, 1190):
                image.putpixel((px, py), (180, 30, 30))
        safe = server.gptmaker._prepare_text_safe_layout(image, 0)
        self.assertEqual(safe.size, image.size)
        self.assertEqual(safe.getpixel((430, 200)), (180, 30, 30))
        self.assertEqual(safe.getpixel((430, 800)), (180, 30, 30))

    def test_checkpoint_detail_uses_two_separate_photo_panels(self):
        image = Image.new("RGB", (860, 1290), "red")
        for py in range(645, 1290):
            for px in range(860):
                image.putpixel((px, py), (0, 0, 255))
        safe = server.gptmaker._prepare_text_safe_layout(image, 6)
        self.assertEqual(safe.getpixel((430, 600)), (255, 0, 0))
        self.assertEqual(safe.getpixel((430, 1000)), (0, 0, 255))
        self.assertNotEqual(safe.getpixel((430, 820)), safe.getpixel((430, 600)))

    def test_template_slot_uses_original_section_number_after_disabled_sections(self):
        self.assertEqual(server.gptmaker._template_index({"number": 6}, 2), 5)
        self.assertEqual(server.gptmaker._section_size(5, low=True), (430, 645))
        self.assertEqual(server.gptmaker._section_size(6, low=True), (430, 800))

    def test_multiple_options_are_used_only_when_cut_plan_requests_them(self):
        one = server.gptmaker._option_display_instruction({"image_prompt": "제품 착용 장면"}, "블랙, 화이트")
        all_options = server.gptmaker._option_display_instruction({"image_prompt": "모든 색상 정리"}, "블랙, 화이트")
        self.assertIn("한 가지", one)
        self.assertIn("함께", all_options)

    def test_selects_lazy_loaded_1688_product_images(self):
        items = [
            {"src": "//cbu01.alicdn.com/img/ibank/O1CN_product_60x60.jpg", "w": 60, "h": 60},
            {"src": "https://cbu01.alicdn.com/img/ibank/O1CN_product_400x400.jpg", "w": 400, "h": 400},
            {"src": "https://cbu01.alicdn.com/img/ibank/O1CN_logo.jpg", "w": 500, "h": 500},
            {"src": "https://example.com/not-a-product.jpg", "w": 800, "h": 800},
        ]

        result = server.gptmaker._select_product_image_urls(items)

        self.assertEqual(result, ["https://cbu01.alicdn.com/img/ibank/O1CN_product.jpg"])

    def test_cleans_1688_product_title(self):
        result = server.gptmaker._clean_product_title("반려동물 원형 방수매트 - 1688")

        self.assertEqual(result, "반려동물 원형 방수매트")
        self.assertEqual(server.gptmaker._clean_product_title("CNINSIDER"), "")

    def test_detects_1688_access_denied_page(self):
        self.assertTrue(server.gptmaker._is_access_blocked("Access denied"))
        self.assertFalse(server.gptmaker._is_access_blocked("반려동물 원형 방수매트"))

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
            "product": {"name": "테스트 상품", "color": "상어, 돌고래"},
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
            section_output = pathlib.Path(directory) / "draft_section_0.jpg"
            self.assertTrue(section_output.exists())
            base_output = pathlib.Path(directory) / "draft_section_0_base.jpg"
            self.assertTrue(base_output.exists())
            server.gptmaker.recompose_plan_section(str(output.with_suffix("")), plan, 0, False)
            with Image.open(section_output) as text_removed:
                self.assertEqual(text_removed.size, (430, 645))
        self.assertEqual(result["section_count"], 1)
        self.assertEqual(image_api.call_count, 1)
        self.assertIn("상어, 돌고래", image_api.call_args.args[0])
        self.assertIn("한 가지 색상·옵션만", image_api.call_args.args[0])
        self.assertEqual(size, (430, 645))

    def test_generates_one_high_quality_final_section(self):
        buffer = io.BytesIO()
        Image.new("RGB", (1024, 1536), "white").save(buffer, "JPEG")
        generated = buffer.getvalue()
        plan = {"product": {"name": "테스트"}, "sections": [{"enabled": True, "type": "메인"}]}
        with tempfile.TemporaryDirectory() as directory:
            reference = pathlib.Path(directory) / "reference.jpg"
            output = pathlib.Path(directory) / "high.jpg"
            reference.write_bytes(generated)
            with patch.object(server.gptmaker, "_oai_image", return_value=generated) as image_api:
                server.gptmaker.run_plan_section_high(plan, 0, [str(reference)], [], str(output))
            self.assertTrue(output.exists())
            with Image.open(output) as final_image:
                self.assertEqual(final_image.size, (860, 1290))
            self.assertEqual(image_api.call_args.kwargs["quality"], "high")

    def test_completed_low_resolution_draft_is_saved_to_history(self):
        with tempfile.TemporaryDirectory() as directory:
            result_dir = pathlib.Path(directory) / "results"
            result_dir.mkdir()
            history_file = pathlib.Path(directory) / "history.json"

            def fake_draft(plan, image_paths, reference_urls, output, on_section=None, style_image_paths=None):
                pathlib.Path(output).write_bytes(b"draft")
                if on_section:
                    for index in range(3):
                        on_section(index, str(result_dir / f"section-{index}.jpg"))
                return {"product_name": "히스토리 상품", "section_count": 3}

            with patch.object(server, "RESULT_DIR", str(result_dir)), \
                 patch.object(server, "HISTORY_FILE", str(history_file)), \
                 patch.object(server.gptmaker, "run_plan_draft", side_effect=fake_draft):
                server.worker_plan_draft("abc123def456", {"sections": []}, [], [])

            history = json.loads(history_file.read_text(encoding="utf-8"))
            self.assertEqual(history[0]["job"], "abc123def456")
            self.assertEqual(history[0]["src"], "저해상도 시안")
            self.assertEqual(history[0]["section_count"], 3)
            self.assertTrue(history[0]["draft"])
            self.assertEqual(server.JOBS["abc123def456"]["ready_sections"], [0, 1, 2])

    def test_low_resolution_sections_are_generated_in_parallel(self):
        buffer = io.BytesIO()
        Image.new("RGB", (1024, 1536), "white").save(buffer, "JPEG")
        generated = buffer.getvalue()
        plan = {
            "product": {"name": "병렬 테스트"},
            "sections": [{"enabled": True, "number": number} for number in range(6)],
        }

        def delayed_image(*args, **kwargs):
            time.sleep(0.1)
            return generated

        with tempfile.TemporaryDirectory() as directory:
            reference = pathlib.Path(directory) / "reference.jpg"
            output = pathlib.Path(directory) / "parallel.jpg"
            reference.write_bytes(generated)
            started = time.monotonic()
            ready = []
            with patch.object(server.gptmaker, "_oai_image", side_effect=delayed_image):
                result = server.gptmaker.run_plan_draft(
                    plan, [str(reference)], [], str(output),
                    on_section=lambda index, path: ready.append(index),
                )
            elapsed = time.monotonic() - started

        self.assertEqual(result["section_count"], 6)
        self.assertEqual(sorted(ready), list(range(6)))
        self.assertLess(elapsed, 0.45)


if __name__ == "__main__":
    unittest.main()
