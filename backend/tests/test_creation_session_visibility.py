from __future__ import annotations

import asyncio

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database.models import NovelCreationSession, Project
from app.database.session import Base
from app.routers.novel_creation import list_creation_sessions


def test_creation_session_list_hides_contexts_linked_to_deleted_projects() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    try:
        db.add(Project(id="live-project", title="仍在作品库"))
        db.add_all([
            NovelCreationSession(
                id="active-standalone",
                status="drafting",
                user_brief="独立立项",
            ),
            NovelCreationSession(
                id="completed-live",
                status="completed",
                created_project_id="live-project",
            ),
            NovelCreationSession(
                id="completed-deleted",
                status="completed",
                created_project_id="deleted-project",
            ),
            NovelCreationSession(
                id="draft-from-deleted",
                status="reviewing",
                source_project_id="deleted-source-project",
            ),
            NovelCreationSession(
                id="mixed-link-deleted",
                status="completed",
                created_project_id="live-project",
                source_project_id="deleted-source-project",
            ),
        ])
        db.commit()

        response = asyncio.run(list_creation_sessions(
            include_completed=True,
            project_id=None,
            db=db,
        ))
        visible_ids = {item["id"] for item in response.data["sessions"]}

        assert visible_ids == {"active-standalone", "completed-live"}
    finally:
        db.close()
        engine.dispose()
