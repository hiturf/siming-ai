"""Tests for quality mode shared prompt pack integration."""
import sys
import os
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services.agent.prompt_builder import build_system_prompt, inject_public_prompt_pack_section
from app.prompts.prompt_source import get_public_chapter_quality_system_prompt


class BuildSystemPromptWithPublicPackTest(unittest.TestCase):
    """Verify build_system_prompt injects public prompt pack."""

    def test_injects_pack_when_db_provided(self):
        pack = MagicMock()
        pack.build_system_prompt.return_value = "你是写作助手。"
        pack.tool_policy = None

        db = MagicMock()
        mock_pack = MagicMock()
        mock_pack.pack_id = "chapter_writing_quality"
        mock_pack.title = "质量模式"
        mock_pack.version = "1.0.0"
        mock_pack.summary = "完整技法"
        mock_pack.quality_rubric_json = {"dimensions": [{"name": "opening_hook", "max_score": 10}]}
        mock_pack.forbidden_patterns_json = ["仿佛"]

        query_mock = MagicMock()
        query_mock.filter.return_value = query_mock
        query_mock.first.return_value = mock_pack
        db.query.return_value = query_mock

        result = build_system_prompt(pack, db=db, public_pack_scope="chapter_writing")
        self.assertIn("质量模式", result)
        self.assertIn("1.0.0", result)

    def test_no_injection_when_no_db(self):
        pack = MagicMock()
        pack.build_system_prompt.return_value = "你是写作助手。"
        # Mock build_tool_policy_section to return empty
        with patch("app.services.agent.prompt_builder.build_tool_policy_section", return_value=""):
            result = build_system_prompt(pack, scope="chapter_writing")
        self.assertEqual(result, "你是写作助手。")


class InjectPublicPromptPackSectionTest(unittest.TestCase):
    """Verify inject_public_prompt_pack_section behavior."""

    def test_appends_pack_info(self):
        db = MagicMock()
        mock_pack = MagicMock()
        mock_pack.pack_id = "chapter_writing_quality"
        mock_pack.title = "质量模式章节写作"
        mock_pack.version = "1.0.0"
        mock_pack.summary = "完整技法"
        mock_pack.quality_rubric_json = {
            "dimensions": [{"name": "opening_hook", "max_score": 10}]
        }
        mock_pack.forbidden_patterns_json = ["仿佛", "不由得"]

        query_mock = MagicMock()
        query_mock.filter.return_value = query_mock
        query_mock.first.return_value = mock_pack
        db.query.return_value = query_mock

        result = inject_public_prompt_pack_section("原始提示词", db, "chapter_writing")
        self.assertIn("质量模式章节写作", result)
        self.assertIn("1.0.0", result)
        self.assertNotIn("opening_hook", result)
        self.assertNotIn("仿佛", result)

    def test_returns_original_on_no_pack(self):
        db = MagicMock()
        query_mock = MagicMock()
        query_mock.filter.return_value = query_mock
        query_mock.first.return_value = None
        db.query.return_value = query_mock

        result = inject_public_prompt_pack_section("原始提示词", db, "nonexistent")
        self.assertEqual(result, "原始提示词")


class PublicChapterPromptUnificationTest(unittest.TestCase):
    def test_public_prompt_uses_the_single_unsaved_draft_contract(self):
        quality = get_public_chapter_quality_system_prompt()
        self.assertIn("API-free 模式", quality)
        self.assertIn("未入库草稿", quality)
        self.assertIn("立即结束", quality)
        self.assertNotIn("快速模式", quality)
        self.assertNotIn("create_chapter", quality)


if __name__ == "__main__":
    unittest.main()
