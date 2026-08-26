"""SQLAlchemy-backed model configuration adapter."""

from __future__ import annotations

from ....core.crypto import decrypt
from ....database.session import SessionLocal
from ..application.ports import ModelConfigurationPort
from ..domain.configuration import ModelProviderConfig, TaskModelSetting
from ..domain.policy import local_runtime_disabled
from .models import APIConfig, ModelTaskSetting
from .readiness import is_model_config_usable


class SqlAlchemyModelConfiguration(ModelConfigurationPort):
    def global_default(self) -> ModelProviderConfig | None:
        with SessionLocal() as db:
            row = db.query(APIConfig).filter(APIConfig.is_global_default == True).first()  # noqa: E712
            return self._snapshot(row)

    def ready_providers(self) -> tuple[ModelProviderConfig, ...]:
        with SessionLocal() as db:
            return tuple(
                snapshot
                for row in db.query(APIConfig).all()
                if (snapshot := self._snapshot(row)) is not None
            )

    def provider(self, provider: str) -> ModelProviderConfig | None:
        with SessionLocal() as db:
            row = db.query(APIConfig).filter(APIConfig.provider == provider).first()
            return self._snapshot(row)

    def task_setting(self, task_type: str) -> TaskModelSetting | None:
        with SessionLocal() as db:
            row = (
                db.query(ModelTaskSetting)
                .filter(ModelTaskSetting.task_type == task_type)
                .first()
            )
            if not row:
                return None
            return TaskModelSetting(
                task_type=row.task_type,
                provider=row.provider,
                model_name=row.model_name,
                context_length=row.context_length,
            )

    @staticmethod
    def _snapshot(row: APIConfig | None) -> ModelProviderConfig | None:
        if not row or local_runtime_disabled(row.provider) or not is_model_config_usable(row):
            return None
        return ModelProviderConfig(
            provider=row.provider,
            default_model=row.default_model,
            api_key=decrypt(row.api_key_encrypted) if row.api_key_encrypted else "",
            base_url=row.base_url_override,
            api_protocol=row.api_protocol or "chat_completions",
            provider_type=row.provider_type or "api",
            cli_command=row.cli_command,
            cli_args=row.cli_args,
        )
