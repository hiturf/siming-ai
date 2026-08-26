"""Tests for MCP prompts — moshu_writing_context and related prompts."""
import sys
import os
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.mcp.prompts import (
    list_prompts, get_prompt, render_prompt,
    render_writing_context, render_continuity_check, render_fanfic_draft,
    McpPromptMessage,
)


class ListPromptsTest(unittest.TestCase):
    """Verify prompt listing and metadata."""

    def test_list_returns_prompts(self):
        prompts = list_prompts()
        self.assertGreater(len(prompts), 0)

    def test_writing_context_exists(self):
        p = get_prompt("moshu_writing_context")
        self.assertIsNotNone(p)
        self.assertEqual(p.name, "moshu_writing_context")

    def test_quickstart_exists(self):
        p = get_prompt("moshu_quickstart")
        self.assertIsNotNone(p)

    def test_external_cataloging_exists(self):
        p = get_prompt("moshu_external_cataloging")
        self.assertIsNotNone(p)

    def test_continuity_check_exists(self):
        p = get_prompt("moshu_continuity_check")
        self.assertIsNotNone(p)

    def test_fanfic_draft_exists(self):
        p = get_prompt("moshu_fanfic_draft")
        self.assertIsNotNone(p)

    def test_writing_context_has_required_args(self):
        p = get_prompt("moshu_writing_context")
        arg_names = {a.name for a in p.args}
        self.assertIn("project_id", arg_names)
        self.assertIn("outline_node_id", arg_names)
        self.assertIn("requirements", arg_names)

    def test_project_id_is_required(self):
        p = get_prompt("moshu_writing_context")
        for arg in p.args:
            if arg.name == "project_id":
                self.assertTrue(arg.required)
                break
        else:
            self.fail("project_id arg not found")

    def test_unknown_prompt_returns_none(self):
        self.assertIsNone(get_prompt("nonexistent_prompt"))


def _project_db(*, exists: bool = True) -> MagicMock:
    db = MagicMock()
    query = MagicMock()
    query.filter.return_value = query
    project = SimpleNamespace(id="p1", title="Test Novel") if exists else None
    query.first.return_value = project
    db.query.return_value = query
    return db


def _context_orchestrator(*, status: str = "ready") -> MagicMock:
    orchestrator = MagicMock()
    orchestrator.prepare.return_value = SimpleNamespace(id="manifest-1")
    orchestrator.manifest_payload.return_value = {
        "status": status,
        "budget": {
            "estimated_input_tokens": 3200,
            "input_budget_tokens": 8500,
        },
        "warnings": ["Required source needs author confirmation."],
        "rendered_context": "Persisted governed context.",
    }
    return orchestrator


class RenderGovernedTaskPromptsTest(unittest.TestCase):
    """All task prompts render the one persisted context-manifest path."""

    @patch("app.services.context_orchestrator.ContextOrchestrator")
    def test_writing_context_uses_persisted_manifest(self, orchestrator_class):
        orchestrator = _context_orchestrator()
        orchestrator_class.return_value = orchestrator

        messages = render_writing_context(
            _project_db(),
            "p1",
            chapter_number="100",
            outline_node_id="outline-100",
            requirements="Write in first person",
        )

        self.assertIsInstance(messages[0], McpPromptMessage)
        self.assertIn("context_manifest_id: manifest-1", messages[0].content)
        self.assertIn("manifest_status: ready", messages[0].content)
        self.assertIn("Persisted governed context.", messages[0].content)
        self.assertIn("stop immediately", messages[0].content)
        orchestrator.prepare.assert_called_once_with(
            project_id="p1",
            task_type="writing",
            execution_route="external_mcp",
            arguments={
                "chapter_number": "100",
                "outline_node_id": "outline-100",
                "requirements": "Write in first person",
            },
        )

    @patch("app.services.context_orchestrator.ContextOrchestrator")
    def test_non_ready_manifest_surfaces_confirmation(self, orchestrator_class):
        orchestrator_class.return_value = _context_orchestrator(status="needs_confirmation")

        content = render_writing_context(_project_db(), "p1")[0].content

        self.assertIn("Author Confirmation Required", content)
        self.assertIn("Required source needs author confirmation.", content)

    @patch("app.services.context_orchestrator.ContextOrchestrator")
    def test_continuity_check_uses_review_task(self, orchestrator_class):
        orchestrator = _context_orchestrator()
        orchestrator_class.return_value = orchestrator

        render_continuity_check(_project_db(), "p1", chapter_id="chapter-2")

        orchestrator.prepare.assert_called_once_with(
            project_id="p1",
            task_type="review",
            execution_route="external_mcp",
            arguments={"chapter_id": "chapter-2"},
        )

    @patch("app.services.context_orchestrator.ContextOrchestrator")
    def test_fanfic_draft_uses_writing_task(self, orchestrator_class):
        orchestrator = _context_orchestrator()
        orchestrator_class.return_value = orchestrator

        render_fanfic_draft(
            _project_db(),
            "p1",
            outline_node_id="outline-3",
            requirements="Keep characterization consistent",
        )

        orchestrator.prepare.assert_called_once_with(
            project_id="p1",
            task_type="writing",
            execution_route="external_mcp",
            arguments={
                "outline_node_id": "outline-3",
                "requirements": "Keep characterization consistent",
            },
        )

    @patch("app.services.context_orchestrator.ContextOrchestrator")
    def test_project_not_found_does_not_prepare_manifest(self, orchestrator_class):
        messages = render_writing_context(_project_db(exists=False), "nonexistent")

        self.assertIn("Error", messages[0].content)
        orchestrator_class.assert_not_called()


class RenderPromptDispatchTest(unittest.TestCase):
    """Verify render_prompt dispatches correctly."""

    def test_quickstart_does_not_require_project_id(self):
        db = MagicMock()
        result = render_prompt(db, "moshu_quickstart", {"task": "导入并建档", "no_api": "true"})
        self.assertIsNotNone(result)
        content = result[0].content
        self.assertIn("get_moshu_usage_guide", content)
        self.assertIn("start_external_cataloging_job", content)
        self.assertIn("start_cataloging_job", content)

    def test_external_cataloging_does_not_require_project_id(self):
        db = MagicMock()
        result = render_prompt(db, "moshu_external_cataloging", {})
        self.assertIsNotNone(result)
        content = result[0].content
        self.assertIn("save_external_cataloging_candidates", content)
        self.assertIn("apply_pending_cataloging", content)

    def test_unknown_returns_none(self):
        db = MagicMock()
        result = render_prompt(db, "unknown", {"project_id": "p1"})
        self.assertIsNone(result)

    def test_missing_project_id_returns_error(self):
        db = MagicMock()
        result = render_prompt(db, "moshu_writing_context", {})
        self.assertIsNotNone(result)
        self.assertIn("Error", result[0].content)


if __name__ == "__main__":
    unittest.main()
