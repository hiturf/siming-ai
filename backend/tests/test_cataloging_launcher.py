"""Regression tests for author-triggered canonical cataloging."""
from __future__ import annotations

from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database.models import Base, CatalogingChapterRun, CatalogingJob, Chapter, OperationRun, Project
from app.services.cataloging.launcher import (
    CHAPTER_SAVE_SOURCE,
    create_and_queue_cataloging_job,
    find_blocking_chapter_cataloging_job,
    mark_cataloging_worker_failure,
    mark_interrupted_cataloging_jobs,
)


def _database():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    return engine, sessionmaker(bind=engine)()


def _chapter(db):
    db.add_all([
        Project(id="project-1", title="Test Novel"),
        Chapter(
            id="chapter-1",
            project_id="project-1",
            title="第一章",
            content="正文",
            word_count=2,
            cataloging_required=True,
        ),
    ])
    db.commit()


def test_author_trigger_creates_canonical_job_without_implicit_worker_for_external_backend():
    engine, db = _database()
    try:
        _chapter(db)
        job, launch = create_and_queue_cataloging_job(
            db,
            "project-1",
            ["chapter-1"],
            backend_override="external_agent",
            trigger_source=CHAPTER_SAVE_SOURCE,
            run_now=True,
        )

        assert job.execution_backend == "external_agent"
        assert job.model_source == "chapter_save:external_agent"
        assert launch["worker_queued"] is False
        assert db.query(CatalogingChapterRun).filter_by(job_id=job.id).one().chapter_id == "chapter-1"
        operation = db.get(OperationRun, job.operation_id)
        assert operation.title == "《第一章》章节建档"
        assert operation.tool_mode == "chapter_save:external_agent"
        assert "作者已启动建档" in operation.current_message
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_new_author_trigger_supersedes_only_same_chapter_job():
    engine, db = _database()
    try:
        _chapter(db)
        first, _ = create_and_queue_cataloging_job(
            db,
            "project-1",
            ["chapter-1"],
            backend_override="external_agent",
            trigger_source=CHAPTER_SAVE_SOURCE,
            run_now=False,
        )
        second, launch = create_and_queue_cataloging_job(
            db,
            "project-1",
            ["chapter-1"],
            backend_override="external_agent",
            trigger_source=CHAPTER_SAVE_SOURCE,
            run_now=False,
        )

        db.refresh(first)
        assert first.status == "cancelled"
        assert second.status == "queued"
        assert launch["superseded_job_ids"] == [first.id]
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_nonterminal_author_job_blocks_other_chapter_but_allows_same_chapter_rewrite():
    engine, db = _database()
    try:
        _chapter(db)
        job, _ = create_and_queue_cataloging_job(
            db,
            "project-1",
            ["chapter-1"],
            backend_override="external_agent",
            trigger_source=CHAPTER_SAVE_SOURCE,
            run_now=False,
        )

        assert find_blocking_chapter_cataloging_job(db, "project-1").id == job.id
        assert find_blocking_chapter_cataloging_job(
            db,
            "project-1",
            allow_chapter_id="chapter-1",
        ) is None
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_worker_failure_is_persisted_as_retryable_pause_with_operation_attention():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    SessionFactory = sessionmaker(bind=engine)
    db = SessionFactory()
    try:
        _chapter(db)
        job, _ = create_and_queue_cataloging_job(
            db,
            "project-1",
            ["chapter-1"],
            backend_override="external_agent",
            trigger_source=CHAPTER_SAVE_SOURCE,
            run_now=False,
        )
        job_id = job.id
        operation_id = job.operation_id
        db.close()

        with patch("app.services.cataloging.launcher.SessionLocal", SessionFactory):
            assert mark_cataloging_worker_failure(
                job_id,
                "database is locked",
                failure_class="DatabaseWriteLockTimeout",
            )

        db = SessionFactory()
        failed = db.get(CatalogingJob, job_id)
        operation = db.get(OperationRun, operation_id)
        run = db.query(CatalogingChapterRun).filter_by(job_id=job_id).one()
        assert failed.status == "paused_on_failure"
        assert run.status == "failed"
        assert operation.status == "paused"
        assert operation.failure_class == "DatabaseWriteLockTimeout"
        assert operation.attention_json["blocking"] is True
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_startup_recovery_marks_orphaned_internal_job_interrupted():
    engine, db = _database()
    try:
        _chapter(db)
        job, _ = create_and_queue_cataloging_job(
            db,
            "project-1",
            ["chapter-1"],
            backend_override="external_agent",
            trigger_source=CHAPTER_SAVE_SOURCE,
            run_now=False,
        )
        job.execution_backend = "internal_llm"
        db.commit()

        assert mark_interrupted_cataloging_jobs(db) == 1
        db.commit()
        db.refresh(job)
        operation = db.get(OperationRun, job.operation_id)
        assert job.status == "paused_on_failure"
        assert operation.failure_class == "interrupted"
        assert operation.status == "paused"
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()
