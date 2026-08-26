"""Regression tests for shared cataloging behavior prompts."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.prompts.cataloging_source import (
    get_external_cataloging_system_prompt,
    get_fact_extraction_rules,
    get_outline_granularity_rules,
)
from app.services.cataloging.orchestrator import _append_incremental_candidate_retry
from app.services.cataloging.staged_prompts import (
    CATALOGING_RESOLUTION_SYSTEM_PROMPT,
    FACT_EXTRACTION_SYSTEM_PROMPT,
)


class CatalogingPromptUnificationTest(unittest.TestCase):
    """Ensure internal and external cataloging share the same critical rules."""

    def test_outline_granularity_is_shared_across_entrypoints(self):
        marker = 'node_type="section"'
        shared = get_outline_granularity_rules()
        external = get_external_cataloging_system_prompt()

        self.assertIn(marker, shared)
        self.assertIn(marker, external)
        self.assertIn(marker, CATALOGING_RESOLUTION_SYSTEM_PROMPT)
        self.assertIn("parent_title", external)
        self.assertIn("parent_title", CATALOGING_RESOLUTION_SYSTEM_PROMPT)

    def test_fact_extraction_rules_are_shared_by_staged_prompt(self):
        shared = get_fact_extraction_rules()

        self.assertIn("只裸读当前章节正文", shared)
        self.assertIn("只裸读当前章节正文", FACT_EXTRACTION_SYSTEM_PROMPT)
        self.assertIn("outline_fact 要覆盖整章节点和重要场景节点", FACT_EXTRACTION_SYSTEM_PROMPT)

    def test_cataloging_prompts_keep_jsonl_protocol_and_readable_examples(self):
        external = get_external_cataloging_system_prompt()
        internal = CATALOGING_RESOLUTION_SYSTEM_PROMPT
        shared_facts = get_fact_extraction_rules()

        for prompt in (external, internal):
            self.assertIn("character_state_update", prompt)
            self.assertIn("worldbuilding_create", prompt)
            self.assertIn("JSONL", prompt)
            self.assertIn("角色", prompt)
            self.assertIn("世界观", prompt)
            self.assertIn("候选", prompt)

        self.assertIn("只输出 JSONL", shared_facts)
        self.assertIn("不要输出 Markdown", shared_facts)

    def test_candidate_prompts_require_summary_and_outline_only_on_initial_generation(self):
        external = get_external_cataloging_system_prompt()
        internal = CATALOGING_RESOLUTION_SYSTEM_PROMPT

        for prompt in (external, internal):
            self.assertIn("首次生成回合的首个响应对象必须同时包含两个必填对象", prompt)
            self.assertIn('"chapter_outline"', prompt)
            self.assertIn("不能只返回其中一个", prompt)

    def test_incremental_repair_prompt_does_not_request_a_full_candidate_replay(self):
        for prompt in (
            get_external_cataloging_system_prompt(),
            CATALOGING_RESOLUTION_SYSTEM_PROMPT,
        ):
            self.assertEqual(prompt.count("【候选缺项自动修复】"), 1)
            self.assertIn("增量修复回合", prompt)
            self.assertIn("不要重发完整候选集", prompt)

        retry_prompt = _append_incremental_candidate_retry(
            "首次生成回合必须输出完整骨架。",
            "缺少角色状态候选：张三",
        )
        self.assertIn("本节规则优先于上面的首次生成要求", retry_prompt)
        self.assertIn("只输出错误信息明确指出的缺失候选", retry_prompt)
        self.assertIn("不要重发完整候选集", retry_prompt)
        self.assertNotIn("重新输出完整标准 JSONL", retry_prompt)

    def test_character_state_schema_tracks_appearance_and_age(self):
        external = get_external_cataloging_system_prompt()

        self.assertIn("character_state_update", external)
        self.assertIn('"appearance":"..."', external)
        self.assertIn('"age":"..."', external)


if __name__ == "__main__":
    unittest.main()
