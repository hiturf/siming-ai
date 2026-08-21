"""Focused tests for application lifecycle reliability guards."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from app.bootstrap.lifecycle import (
    _install_windows_transport_exception_filter,
    _is_benign_windows_pipe_reset,
    _recover_gateway_sync_capture_queue,
)


def _windows_reset() -> ConnectionResetError:
    error = ConnectionResetError("peer closed")
    error.winerror = 10054
    return error


def test_only_the_known_windows_proactor_close_race_is_classified_as_benign():
    context = {
        "message": (
            "Exception in callback "
            "_ProactorBasePipeTransport._call_connection_lost(None)"
        ),
        "exception": _windows_reset(),
    }

    with patch("app.bootstrap.lifecycle.sys.platform", "win32"):
        assert _is_benign_windows_pipe_reset(context)
        assert not _is_benign_windows_pipe_reset(
            {"message": "request failed", "exception": _windows_reset()}
        )
        assert not _is_benign_windows_pipe_reset(
            {
                "message": context["message"],
                "exception": RuntimeError("application failure"),
            }
        )

    with patch("app.bootstrap.lifecycle.sys.platform", "linux"):
        assert not _is_benign_windows_pipe_reset(context)


def test_transport_filter_delegates_every_other_event_to_previous_handler():
    loop = MagicMock()
    previous_handler = MagicMock()
    loop.get_exception_handler.return_value = previous_handler

    installed_handler, returned_previous = _install_windows_transport_exception_filter(loop)
    normal_context = {"message": "task crashed", "exception": RuntimeError("boom")}
    installed_handler(loop, normal_context)

    assert returned_previous is previous_handler
    previous_handler.assert_called_once_with(loop, normal_context)
    loop.default_exception_handler.assert_not_called()


def test_transport_filter_swallows_only_the_known_benign_event():
    loop = MagicMock()
    previous_handler = MagicMock()
    loop.get_exception_handler.return_value = previous_handler
    installed_handler, _ = _install_windows_transport_exception_filter(loop)
    context = {
        "message": (
            "Exception in callback "
            "_ProactorBasePipeTransport._call_connection_lost(None)"
        ),
        "exception": _windows_reset(),
    }

    with patch("app.bootstrap.lifecycle.sys.platform", "win32"):
        installed_handler(loop, context)

    previous_handler.assert_not_called()
    loop.default_exception_handler.assert_not_called()


def test_gateway_sync_recovery_failure_does_not_block_application_startup(caplog):
    settings = MagicMock(gateway_enabled=True)
    with patch("app.bootstrap.lifecycle.get_settings", return_value=settings), patch(
        "app.modules.gateway.infrastructure.change_capture.recover_sync_capture_queue",
        side_effect=RuntimeError("stale capture contract"),
    ):
        _recover_gateway_sync_capture_queue()

    assert "Failed to recover Gateway sync capture queue" in caplog.text
