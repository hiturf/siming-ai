"""Repair invalid JSON created by legacy assistant run-step truncation.

Revision ID: 300a21_valid_step_json
Revises: 300a20_remove_blueprints
"""

from __future__ import annotations

import hashlib
import json

import sqlalchemy as sa

from alembic import op

revision = "300a21_valid_step_json"
down_revision = "300a20_remove_blueprints"
branch_labels = None
depends_on = None


_LEGACY_SUFFIX = "...[truncated]"
_META_KEY = "_siming_run_log"


def _legacy_envelope(raw: str, *, kind: str) -> str:
    preview = raw[: -len(_LEGACY_SUFFIX)] if raw.endswith(_LEGACY_SUFFIX) else raw
    return json.dumps(
        {
            _META_KEY: {"version": 1, "kind": kind},
            "_truncated": True,
            "reason": "legacy_invalid_json_truncation",
            "original_chars": None,
            "stored_chars": len(raw),
            "stored_sha256": hashlib.sha256(raw.encode("utf-8")).hexdigest(),
            "preview": preview[:4096],
        },
        ensure_ascii=False,
    )


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "assistant_run_steps" not in inspector.get_table_names():
        return
    columns = {column["name"] for column in inspector.get_columns("assistant_run_steps")}
    if not {"id", "request_json", "result_json"}.issubset(columns):
        return

    rows = bind.execute(
        sa.text(
            "SELECT id, request_json, result_json FROM assistant_run_steps "
            "WHERE request_json LIKE :suffix OR result_json LIKE :suffix"
        ),
        {"suffix": f"%{_LEGACY_SUFFIX}"},
    ).mappings().all()
    for row in rows:
        request_json = row["request_json"]
        result_json = row["result_json"]
        if isinstance(request_json, str) and request_json.endswith(_LEGACY_SUFFIX):
            bind.execute(
                sa.text(
                    "UPDATE assistant_run_steps SET request_json = :payload WHERE id = :step_id"
                ),
                {
                    "payload": _legacy_envelope(
                        request_json,
                        kind="unrecoverable_request",
                    ),
                    "step_id": row["id"],
                },
            )
        if isinstance(result_json, str) and result_json.endswith(_LEGACY_SUFFIX):
            bind.execute(
                sa.text(
                    "UPDATE assistant_run_steps SET result_json = :payload WHERE id = :step_id"
                ),
                {
                    "payload": _legacy_envelope(
                        result_json,
                        kind="truncated_result",
                    ),
                    "step_id": row["id"],
                },
            )


def downgrade() -> None:
    # The bytes discarded by the old implementation cannot be reconstructed.
    # Keeping the repaired envelopes is safer than recreating invalid JSON.
    pass
