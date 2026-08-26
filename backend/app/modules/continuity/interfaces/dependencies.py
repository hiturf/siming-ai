"""Configured continuity module dependencies."""
from __future__ import annotations

from ..application.prompting import ContinuityPromptService

_service: ContinuityPromptService | None = None


def configure_continuity_prompt_service(service: ContinuityPromptService) -> None:
    global _service
    _service = service


def _prompt_service() -> ContinuityPromptService:
    if _service is not None:
        return _service
    from ...assistant.interfaces.prompts import get_prompt_compiler

    return ContinuityPromptService(get_prompt_compiler())


def render_cataloging_candidates_prompt() -> str:
    return _prompt_service().render_cataloging_candidates()


def render_external_cataloging_prompt() -> str:
    return _prompt_service().render_external_cataloging()


__all__ = [
    "configure_continuity_prompt_service",
    "render_external_cataloging_prompt",
    "render_cataloging_candidates_prompt",
]
