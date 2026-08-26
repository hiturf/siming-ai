"""SQLAlchemy model configuration CRUD implementation."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from .legacy_models import LocalModel
from .models import APIConfig, ModelTaskSetting


class SqlAlchemyModelConfigCrud:
    def __init__(self, session: Session) -> None:
        self.session = session

    def list_configs(self):
        return self.session.query(APIConfig).order_by(APIConfig.created_at.desc()).all()

    def get_provider(self, provider: str):
        return self.session.query(APIConfig).filter(APIConfig.provider == provider).first()

    def create(self, **values: Any):
        config = APIConfig(**values)
        self.session.add(config)
        return config

    def delete(self, config: Any) -> None:
        self.session.delete(config)

    def get_global(self):
        return self.session.query(APIConfig).filter(APIConfig.is_global_default == True).first()  # noqa: E712

    def get_ready_global(self):
        return self.session.query(APIConfig).filter(
            APIConfig.is_global_default == True,  # noqa: E712
            APIConfig.readiness_status == "ready",
        ).first()

    def clear_global(self) -> None:
        self.session.query(APIConfig).update({"is_global_default": False})

    def make_global_if_no_ready_default(self, config: APIConfig) -> bool:
        """Promote a verified model only when the user has no usable default."""

        if self.get_ready_global() is not None:
            return False
        self.clear_global()
        config.is_global_default = True
        return True

    def list_local_models(self):
        return self.session.query(LocalModel).order_by(LocalModel.recommended_vram_gb.asc()).all()

    def list_task_settings(self):
        return self.session.query(ModelTaskSetting).order_by(ModelTaskSetting.task_type.asc()).all()

    def get_task_setting(self, task_type: str):
        return self.session.query(ModelTaskSetting).filter(
            ModelTaskSetting.task_type == task_type
        ).first()

    def create_task_setting(self, **values: Any):
        setting = ModelTaskSetting(**values)
        self.session.add(setting)
        return setting

    def delete_task_settings_for_provider(self, provider: str) -> int:
        return self.session.query(ModelTaskSetting).filter(
            ModelTaskSetting.provider == provider
        ).delete(synchronize_session=False)


__all__ = ["SqlAlchemyModelConfigCrud"]
