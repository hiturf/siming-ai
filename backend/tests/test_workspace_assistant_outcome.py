from app.routers.ai_writer import _workspace_outcome


def test_workspace_outcome_marks_empty_response():
    outcome = _workspace_outcome(
        "",
        applied_actions=[],
        tool_logs=[],
        searched_context=[],
    )

    assert outcome == "empty_response"


def test_workspace_outcome_marks_tool_completion_without_text_reply():
    outcome = _workspace_outcome(
        "",
        applied_actions=[{"tool": "chapter_writer", "status": "ok"}],
        tool_logs=[],
        searched_context=[],
    )

    assert outcome == "completed_with_tools"


def test_workspace_outcome_marks_failures():
    outcome = _workspace_outcome(
        "已处理",
        applied_actions=[],
        tool_logs=[{"tool": "json_repair", "status": "error"}],
        searched_context=[],
        failed_logs=[{"tool": "json_repair", "status": "error"}],
    )

    assert outcome == "failed"


def test_workspace_outcome_marks_partial_success_when_a_write_succeeded_before_failure():
    outcome = _workspace_outcome(
        "草稿已生成，但编辑器通知失败",
        applied_actions=[{"tool": "chapter_writer", "status": "ok"}],
        tool_logs=[{"tool": "notify_editor", "status": "error"}],
        searched_context=[],
        failed_logs=[{"tool": "notify_editor", "status": "error"}],
    )

    assert outcome == "partial_success"
