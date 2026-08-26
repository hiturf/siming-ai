"""Ports required by model selection and provider execution."""

from __future__ import annotations

from typing import Protocol

from ..domain.configuration import ModelProviderConfig, TaskModelSetting


class ModelConfigurationPort(Protocol):
    def global_default(self) -> ModelProviderConfig | None: ...

    def ready_providers(self) -> tuple[ModelProviderConfig, ...]: ...

    def provider(self, provider: str) -> ModelProviderConfig | None: ...

    def task_setting(self, task_type: str) -> TaskModelSetting | None: ...
