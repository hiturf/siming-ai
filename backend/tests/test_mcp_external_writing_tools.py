"""Tests for external writing tools exposure through MCP."""
import sys
import os
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.mcp.adapter import list_mcp_tools


class MCPExternalWritingToolsTest(unittest.TestCase):
    """Verify external writing tools are exposed through MCP."""

    def test_context_tool_in_readonly(self):
        tools = list_mcp_tools(permission_pack="readonly_collaboration")
        names = {t.name for t in tools}
        self.assertIn("prepare_external_writing_context", names)

    def test_draft_save_requires_write_permission(self):
        readonly = {t.name for t in list_mcp_tools(permission_pack="readonly_collaboration")}
        drafting = {t.name for t in list_mcp_tools(permission_pack="chapter_drafting")}
        self.assertNotIn("save_external_chapter_draft", readonly)
        self.assertIn("get_external_chapter_draft", readonly)
        self.assertIn("save_external_chapter_draft", drafting)

    def test_review_tool_in_readonly(self):
        tools = list_mcp_tools(permission_pack="readonly_collaboration")
        names = {t.name for t in tools}
        self.assertIn("record_external_quality_review", names)

    def test_formal_chapter_writes_are_not_agent_tools(self):
        tools = list_mcp_tools(permission_pack="project_writing")
        names = {t.name for t in tools}
        self.assertNotIn("create_chapter", names)
        self.assertNotIn("update_chapter", names)


if __name__ == "__main__":
    unittest.main()
