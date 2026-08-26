"""Make AI chapter drafts and cataloging explicitly author controlled.

Revision ID: 300a18_user_chapter_cataloging
Revises: 300a17_chapter_sort_order
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "300a18_user_chapter_cataloging"
down_revision = "300a17_chapter_sort_order"
branch_labels = None
depends_on = None


def _columns(inspector, table: str) -> set[str]:
    return {column["name"] for column in inspector.get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if "chapters" in tables and "cataloging_required" not in _columns(inspector, "chapters"):
        op.add_column(
            "chapters",
            sa.Column("cataloging_required", sa.Boolean(), nullable=False, server_default=sa.false()),
        )

    if "chapter_drafts" in tables:
        columns = _columns(sa.inspect(bind), "chapter_drafts")
        if "saved_chapter_id" not in columns:
            op.add_column("chapter_drafts", sa.Column("saved_chapter_id", sa.String(36), nullable=True))
        if "status" not in columns:
            op.add_column(
                "chapter_drafts",
                sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
            )
            # Old transient cache rows were never author-visible pending work.
            # Treating them as pending would lock every upgraded project.
            op.execute(sa.text("UPDATE chapter_drafts SET status = 'superseded'"))

    if "cataloging_chapter_runs" in tables and "chapter_version" not in _columns(sa.inspect(bind), "cataloging_chapter_runs"):
        op.add_column(
            "cataloging_chapter_runs",
            sa.Column("chapter_version", sa.Integer(), nullable=True),
        )

    if "public_prompt_packs" in tables:
        op.execute(
            sa.text(
                "DELETE FROM public_prompt_packs "
                "WHERE pack_id = 'chapter_writing_fast' AND is_builtin = 1"
            )
        )

    # These tables/columns belonged to the deleted automatic planning and
    # assistant-owned chapter persistence paths.  Remove the stored schema as
    # well, so upgraded installations cannot accidentally revive those paths.
    for table in ("chapter_write_claims", "agent_plan_steps", "agent_plans"):
        if table in tables:
            op.drop_table(table)
    if "assistant_runs" in tables and "assistant_mode" in _columns(sa.inspect(bind), "assistant_runs"):
        op.drop_column("assistant_runs", "assistant_mode")


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if "cataloging_chapter_runs" in tables and "chapter_version" in _columns(inspector, "cataloging_chapter_runs"):
        op.drop_column("cataloging_chapter_runs", "chapter_version")
    if "chapter_drafts" in tables:
        columns = _columns(sa.inspect(bind), "chapter_drafts")
        for name in ("status", "saved_chapter_id"):
            if name in columns:
                op.drop_column("chapter_drafts", name)
    if "chapters" in tables and "cataloging_required" in _columns(sa.inspect(bind), "chapters"):
        op.drop_column("chapters", "cataloging_required")
