"""Provider-neutral model configuration values."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ModelProviderConfig:
    provider: str
    default_model: str
    api_key: str
    base_url: str | None = None
    api_protocol: str = "chat_completions"
    provider_type: str = "api"
    cli_command: str | None = None
    cli_args: str | None = None


@dataclass(frozen=True)
class TaskModelSetting:
    task_type: str
    provider: str
    model_name: str
    context_length: int | None = None

    @property
    def model(self) -> str:
        return f"{self.provider}:{self.model_name}"


@dataclass(frozen=True)
class TaskModelSelection:
    model: str | None
    source: str
    provider: str | None = None
    model_name: str | None = None
