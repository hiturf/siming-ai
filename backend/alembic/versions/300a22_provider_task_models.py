"""Persist provider model catalogs and provider-neutral task defaults.

Revision ID: 300a22_provider_task_models
Revises: 300a21_valid_step_json
"""

from __future__ import annotations

from datetime import datetime

import sqlalchemy as sa
from alembic import op


revision = "300a22_provider_task_models"
down_revision = "300a21_valid_step_json"
branch_labels = None
depends_on = None


def _tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def _columns(table_name: str) -> set[str]:
    if table_name not in _tables():
        return set()
    return {
        column["name"]
        for column in sa.inspect(op.get_bind()).get_columns(table_name)
    }


def _create_task_table() -> None:
    if "model_task_settings" in _tables():
        return
    op.create_table(
        "model_task_settings",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("task_type", sa.String(length=30), nullable=False),
        sa.Column("provider", sa.String(length=50), nullable=False),
        sa.Column("model_name", sa.String(length=512), nullable=False),
        sa.Column("adapter_ids", sa.JSON(), nullable=True),
        sa.Column("context_length", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("task_type"),
    )


def _migrate_local_task_settings() -> None:
    if "local_model_task_settings" not in _tables():
        return
    bind = op.get_bind()
    old = sa.Table(
        "local_model_task_settings",
        sa.MetaData(),
        autoload_with=bind,
    )
    new = sa.Table(
        "model_task_settings",
        sa.MetaData(),
        autoload_with=bind,
    )
    task_aliases = {
        "chat": "assistant",
        "novel_creation": "planning",
        "new_project": "planning",
    }
    existing = {
        str(row[0])
        for row in bind.execute(sa.select(new.c.task_type)).all()
    }
    for row in bind.execute(sa.select(old)).mappings():
        task_type = task_aliases.get(str(row.get("task_type") or ""), row.get("task_type"))
        model_name = str(row.get("model_key") or "").strip()
        if not task_type or not model_name or task_type in existing:
            continue
        bind.execute(
            new.insert().values(
                id=row.get("id"),
                task_type=task_type,
                provider="local_llama_cpp",
                model_name=model_name,
                adapter_ids=row.get("adapter_ids"),
                context_length=row.get("context_length"),
                created_at=row.get("created_at") or datetime.utcnow(),
                updated_at=row.get("updated_at") or datetime.utcnow(),
            )
        )
        existing.add(str(task_type))
    op.drop_table("local_model_task_settings")


def upgrade() -> None:
    if "api_configs" in _tables() and "available_models_json" not in _columns("api_configs"):
        op.add_column(
            "api_configs",
            sa.Column("available_models_json", sa.JSON(), nullable=True),
        )
    _create_task_table()
    _migrate_local_task_settings()


def downgrade() -> None:
    if "local_model_task_settings" not in _tables():
        op.create_table(
            "local_model_task_settings",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("task_type", sa.String(length=30), nullable=False),
            sa.Column("model_key", sa.String(length=512), nullable=False),
            sa.Column("adapter_ids", sa.JSON(), nullable=True),
            sa.Column("context_length", sa.Integer(), nullable=True),
            sa.Column("allow_api_fallback", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("task_type"),
        )
    if "model_task_settings" in _tables():
        bind = op.get_bind()
        generic = sa.Table("model_task_settings", sa.MetaData(), autoload_with=bind)
        local = sa.Table("local_model_task_settings", sa.MetaData(), autoload_with=bind)
        for row in bind.execute(
            sa.select(generic).where(generic.c.provider == "local_llama_cpp")
        ).mappings():
            bind.execute(
                local.insert().values(
                    id=row.get("id"),
                    task_type=row.get("task_type"),
                    model_key=row.get("model_name"),
                    adapter_ids=row.get("adapter_ids"),
                    context_length=row.get("context_length"),
                    allow_api_fallback=False,
                    created_at=row.get("created_at") or datetime.utcnow(),
                    updated_at=row.get("updated_at") or datetime.utcnow(),
                )
            )
        op.drop_table("model_task_settings")
    if "api_configs" in _tables() and "available_models_json" in _columns("api_configs"):
        op.drop_column("api_configs", "available_models_json")
