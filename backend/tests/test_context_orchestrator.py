"""Focused coverage for auditable task-context governance."""
import asyncio
import json
import unittest
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database.models import (
    AgentRun,
    Base,
    Chapter,
    Character,
    CharacterAIConfig,
    CharacterRelationship,
    ContextManifest,
    LocalModel,
    ModelTaskSetting,
    ModelContextProfile,
    NovelCreationSession,
    OutlineNode,
    Project,
)
from app.services.context_orchestrator import ContextOrchestrator, TASK_CONTEXT_CONTRACTS


class ContextOrchestratorTestCase(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)
        self.db = self.Session()
        self.db.add(Project(id="p1", title="Test project", writing_style="natural"))
        self.db.add(OutlineNode(
            id="o1",
            project_id="p1",
            title="Opening",
            node_type="chapter",
            summary="The protagonist crosses the city gate and sees the enemy banner.",
        ))
        self.db.commit()
        self.service = ContextOrchestrator(self.db)

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def test_unknown_remote_model_uses_large_platform_window_and_hard_budget(self):
        manifest = self.service.prepare(
            project_id="p1",
            task_type="writing",
            model="unknown-provider:unknown-model",
            arguments={"outline_node_id": "o1", "requirements": "Write the opening."},
        )
        self.assertEqual(manifest.context_window_tokens, 1_000_000)
        self.assertEqual(manifest.output_reserve_tokens, 16_000)
        self.assertEqual(manifest.input_budget_tokens, 983_488)
        self.assertLessEqual(manifest.estimated_input_tokens, manifest.input_budget_tokens)
        self.assertEqual(manifest.status, "ready")
        self.assertTrue(any("platform 1M context default" in warning for warning in manifest.warnings_json))

    def test_deepseek_creation_budget_uses_registered_large_output_capacity(self):
        profile = self.service.resolve_model_profile(
            "deepseek:deepseek-v4-flash",
            "new_project",
        )
        budget = self.service.budget_for(TASK_CONTEXT_CONTRACTS["new_project"], profile)

        self.assertEqual(profile.context_window_tokens, 1_000_000)
        self.assertEqual(profile.max_output_tokens, 384_000)
        self.assertEqual(budget.output_reserve_tokens, 300_000)
        self.assertEqual(budget.hard_input_budget_tokens, 699_488)

    def test_formal_creation_brief_is_a_required_writing_style_anchor(self):
        creation = NovelCreationSession(
            id="creation-p1",
            created_project_id="p1",
            status="completed",
            revision=4,
            draft_json={
                "form": {
                    "target_words": 2_500_000,
                    "target_chapters": 1_000,
                    "writing_style": "克制冷峻，以动作推进",
                    "special_requirements": ["升级必须有代价"],
                },
                "stages": {
                    "constraints": {
                        "status": "confirmed",
                        "data": {
                            "target_words": 2_500_000,
                            "target_chapters": 1_000,
                            "writing_style": "克制冷峻，以动作推进",
                            "special_requirements": ["升级必须有代价"],
                        },
                    },
                    "concepts": {
                        "status": "confirmed",
                        "data": {
                            "selected_concept_id": "concept-1",
                            "options": [{
                                "id": "concept-1",
                                "title": "经脉迷局",
                                "core_conflict": "求真与宗族秩序冲突",
                            }],
                        },
                    },
                    "world_style": {
                        "status": "confirmed",
                        "data": {"style_rules": ["少解释，多可验证细节"]},
                    },
                },
            },
        )
        self.db.add(creation)
        self.db.commit()

        manifest = self.service.prepare(
            project_id="p1",
            task_type="writing",
            model="openai:test",
            arguments={"outline_node_id": "o1"},
        )

        style = next(item for item in manifest.items if item.category == "style")
        self.assertTrue(style.required)
        self.assertIn("2500000", style.content_excerpt)
        self.assertIn("1000", style.content_excerpt)
        self.assertIn("经脉迷局", style.content_excerpt)
        self.assertIn("少解释，多可验证细节", style.content_excerpt)

        updated = dict(creation.draft_json)
        updated["form"] = {**updated["form"], "target_chapters": 1_200}
        updated["stages"] = {
            **updated["stages"],
            "constraints": {
                **updated["stages"]["constraints"],
                "data": {
                    **updated["stages"]["constraints"]["data"],
                    "target_chapters": 1_200,
                },
            },
        }
        creation.draft_json = updated
        creation.revision = 5
        self.db.flush()

        usable, detail = self.service.validate(manifest)
        self.assertFalse(usable)
        self.assertEqual(manifest.status, "stale")
        self.assertIn("Source changed", detail)

    def test_local_model_manifest_uses_task_context_instead_of_fixed_16k(self):
        self.db.add(LocalModel(
            model_key="local-qwen",
            display_name="Local Qwen",
            context_length=262144,
            status="installed",
        ))
        self.db.add(ModelTaskSetting(
            task_type="writing",
            provider="local_llama_cpp",
            model_name="local-qwen",
            context_length=8192,
        ))
        self.db.commit()

        manifest = self.service.prepare(
            project_id="p1",
            task_type="writing",
            model="local_llama_cpp:local-qwen",
            arguments={"outline_node_id": "o1"},
        )

        self.assertEqual(manifest.context_window_tokens, 8192)
        self.assertTrue(manifest.contract_json["model_profile_known"])

    def test_local_model_default_tracks_hardware_and_model_capacity(self):
        self.db.add(LocalModel(
            model_key="small-context",
            display_name="Small Context",
            context_length=12000,
            status="installed",
        ))
        self.db.commit()

        with patch(
            "app.services.local_runtime.hardware.detect_hardware",
            return_value=type("Profile", (), {"recommended_context": 32768})(),
        ), patch(
            "app.services.local_runtime.manager.LocalRuntimeManager.status",
            return_value={"running": False},
        ):
            profile = self.service.resolve_model_profile(
                "local_llama_cpp:small-context",
                "planning",
            )

        self.assertEqual(profile.context_window_tokens, 12000)
        self.assertTrue(profile.known)

    def test_local_context_profile_cannot_exceed_runtime_task_setting(self):
        self.db.add(LocalModel(
            model_key="local-qwen",
            display_name="Local Qwen",
            context_length=262144,
            status="installed",
        ))
        self.db.add(ModelTaskSetting(
            task_type="cataloging",
            provider="local_llama_cpp",
            model_name="local-qwen",
            context_length=16384,
        ))
        self.db.add(ModelContextProfile(
            provider="local_llama_cpp",
            model_name="local-qwen",
            context_window_tokens=65536,
            safety_margin_tokens=512,
        ))
        self.db.commit()

        profile = self.service.resolve_model_profile(
            "local_llama_cpp:local-qwen",
            "cataloging",
        )

        self.assertEqual(profile.context_window_tokens, 16384)

    def test_writing_manifest_consumes_full_character_card_and_relationships(self):
        hero = Character(
            id="c-hero",
            project_id="p1",
            name="姜尘",
            role_type="protagonist",
            current_location="边荒城",
            current_goal="查清遗骨异动",
            mental_state="警惕但克制",
            profile_json={
                "core_motivation": "保护城中百姓",
                "voice": "短句、少解释",
                "moral_taboo": "不以无辜者为饵",
            },
        )
        elder = Character(
            id="c-elder",
            project_id="p1",
            name="石翁",
            role_type="supporting",
        )
        hero.ai_config = CharacterAIConfig(
            id="cfg-hero",
            character_id=hero.id,
            tone_style="沉静克制",
            catchphrases='["先看证据"]',
            verbosity="brief",
            emotion_tendency="外冷内热",
            custom_system_prompt="遇到风险先观察再行动。",
        )
        self.db.add_all([hero, elder])
        self.db.flush()
        self.db.add(CharacterRelationship(
            id="rel-hero-elder",
            project_id="p1",
            character_a_id=hero.id,
            character_b_id=elder.id,
            relationship_type="师友",
            description="石翁传授姜尘辨骨之法。",
        ))
        self.db.commit()

        manifest = self.service.prepare(
            project_id="p1",
            task_type="writing",
            model="openai:test",
            arguments={"outline_node_id": "o1", "character_ids": [hero.id]},
        )

        item = next(item for item in manifest.items if item.category == "scene_character")
        self.assertIn("保护城中百姓", item.content_excerpt)
        self.assertIn("短句、少解释", item.content_excerpt)
        self.assertIn("沉静克制", item.content_excerpt)
        self.assertIn("brief", item.content_excerpt)
        self.assertIn("石翁", item.content_excerpt)
        self.assertIn("师友", item.content_excerpt)

        hero.ai_config.tone_style = "冷峻直接"
        self.db.flush()
        self.assertEqual(manifest.status, "stale")

    def test_new_character_relationship_invalidates_existing_writing_manifest(self):
        first = Character(id="c-first", project_id="p1", name="甲")
        second = Character(id="c-second", project_id="p1", name="乙")
        self.db.add_all([first, second])
        self.db.commit()
        manifest = self.service.prepare(
            project_id="p1",
            task_type="writing",
            model="openai:test",
            arguments={"outline_node_id": "o1", "character_ids": [first.id]},
        )
        self.assertEqual(manifest.status, "ready")

        self.db.add(CharacterRelationship(
            id="rel-new",
            project_id="p1",
            character_a_id=first.id,
            character_b_id=second.id,
            relationship_type="盟友",
        ))
        self.db.flush()

        self.assertEqual(manifest.status, "stale")

    def test_missing_writing_anchor_requires_confirmation(self):
        manifest = self.service.prepare(
            project_id="p1",
            task_type="writing",
            model="openai:test",
            arguments={"requirements": "Write the opening."},
        )
        self.assertEqual(manifest.status, "needs_confirmation")
        self.assertEqual(manifest.coverage_json["target_outline"]["status"], "missing")

    def test_required_anchor_is_never_silently_removed_by_budget(self):
        self.db.add(ModelContextProfile(
            provider="openai",
            model_name="small",
            context_window_tokens=2600,
            max_output_tokens=2048,
            safety_margin_tokens=512,
        ))
        outline = self.db.query(OutlineNode).filter(OutlineNode.id == "o1").first()
        outline.summary = "x" * 5000
        self.db.commit()

        manifest = self.service.prepare(
            project_id="p1",
            task_type="writing",
            model="openai:small",
            arguments={"outline_node_id": "o1"},
        )
        self.assertEqual(manifest.status, "needs_confirmation")
        self.assertEqual(manifest.coverage_json["target_outline"]["status"], "missing")
        self.assertLessEqual(manifest.estimated_input_tokens, manifest.input_budget_tokens)

    def test_source_change_marks_manifest_stale(self):
        manifest = self.service.prepare(
            project_id="p1",
            task_type="writing",
            model="openai:test",
            arguments={"outline_node_id": "o1"},
        )
        outline = self.db.query(OutlineNode).filter(OutlineNode.id == "o1").first()
        outline.summary = "A changed outline fact."
        self.db.flush()

        self.assertEqual(manifest.status, "stale")
        usable, detail = self.service.validate(manifest)
        self.assertFalse(usable)
        self.assertEqual(manifest.status, "stale")
        self.assertIn("Source changed", detail)

    def test_override_is_auditable_but_stale_cannot_be_overridden(self):
        manifest = self.service.prepare(
            project_id="p1",
            task_type="writing",
            model="openai:test",
            arguments={},
        )
        self.service.override(manifest, reason="Author intentionally writes without an outline.", actor="author")
        self.assertEqual(manifest.status, "overridden")
        self.assertEqual(manifest.override_actor, "author")
        self.assertTrue(self.service.validate(manifest)[0])

        manifest.status = "stale"
        with self.assertRaises(ValueError):
            self.service.override(manifest, reason="Ignore stale source.")

    def test_external_formal_write_requires_verified_evidence(self):
        manifest = self.service.prepare(
            project_id="p1",
            task_type="writing",
            model="openai:test",
            execution_route="external_mcp",
            arguments={"outline_node_id": "o1"},
        )
        usable, _ = self.service.validate(manifest, require_external_evidence=True)
        self.assertFalse(usable)

        target = next(item for item in manifest.items if item.category == "target_outline")
        partial = self.service.submit_evidence(manifest, [{
            "source_type": target.source_type,
            "source_id": target.source_id,
            "source_hash": target.source_hash,
        }])
        self.assertEqual(partial["accepted_count"], 1)
        self.assertFalse(self.service.validate(manifest, require_external_evidence=True)[0])

        required_sources = [
            {
                "source_type": item.source_type,
                "source_id": item.source_id,
                "source_hash": item.source_hash,
            }
            for item in manifest.items
            if item.required
        ]
        result = self.service.submit_evidence(manifest, required_sources)
        self.assertEqual(result["accepted_count"], len(required_sources))
        self.assertTrue(self.service.validate(manifest, require_external_evidence=True)[0])

    def test_rebuild_is_resumable_and_does_not_require_semantic_runtime(self):
        job = self.service.create_rebuild_job(requested_by="test")
        self.assertEqual(job.status, "queued")
        self.service.run_rebuild_job(job)
        self.assertEqual(job.status, "completed")
        self.assertEqual(job.completed_projects, 1)
        self.assertEqual(self.service.project_rebuild_block_reason("p1"), "")

        # Startup recovery should observe this completed current-version job
        # rather than queueing and blocking the project again on every launch.
        follow_up = self.service.create_rebuild_job(requested_by="startup")
        self.assertEqual(follow_up.id, job.id)

    def test_search_stays_available_while_rebuild_blocks_generation(self):
        from app.services.workspace.tools.rag_tools import search_context

        job = self.service.create_rebuild_job(requested_by="test")
        self.assertEqual(job.status, "queued")
        result = asyncio.run(search_context(self.db, "p1", {"query": "敌军"}))

        self.assertEqual(result["status"], "ok")
        self.assertTrue(result["data"]["rebuild_in_progress"])
        self.assertEqual(result["data"]["manifest_status"], "blocked_rebuild")

    def test_scoped_agent_tasks_get_distinct_manifest_and_prompt_manifest_can_be_reused(self):
        from app.services.workspace.tools.context_governance import prepare_task_context

        first_chapter = Chapter(project_id="p1", title="Chapter one", content="The gate opens.")
        second_chapter = Chapter(project_id="p1", title="Chapter two", content="The enemy arrives.")
        run = AgentRun(project_id="p1", source="mcp", title="cataloging")
        self.db.add_all([first_chapter, second_chapter, run])
        self.db.flush()

        first = asyncio.run(prepare_task_context(self.db, "p1", {
            "task_type": "cataloging",
            "run_id": run.id,
            "arguments": {"chapter_id": first_chapter.id},
        }))
        second = asyncio.run(prepare_task_context(self.db, "p1", {
            "task_type": "cataloging",
            "run_id": run.id,
            "arguments": {"chapter_id": second_chapter.id},
        }))

        first_id = first["data"]["manifest_id"]
        second_id = second["data"]["manifest_id"]
        self.assertNotEqual(first_id, second_id)
        self.assertEqual(run.context_manifest_id, second_id)

        reused = asyncio.run(prepare_task_context(self.db, "p1", {
            "task_type": "cataloging",
            "context_manifest_id": first_id,
        }))
        self.assertEqual(reused["data"]["manifest_id"], first_id)

    def test_run_bound_manifest_recovers_from_invalid_model_supplied_id(self):
        from app.services.workspace.tools.context_governance import (
            prepare_task_context,
            submit_context_evidence,
        )

        run = AgentRun(project_id="p1", source="mcp", title="writing")
        self.db.add(run)
        self.db.flush()
        prepared = asyncio.run(prepare_task_context(self.db, "p1", {
            "task_type": "writing",
            "run_id": run.id,
            "arguments": {"outline_node_id": "o1"},
        }))
        manifest_id = prepared["data"]["context_manifest_id"]
        self.assertEqual(run.context_manifest_id, manifest_id)

        submitted = asyncio.run(submit_context_evidence(self.db, "p1", {
            "run_id": run.id,
            "context_manifest_id": "model-invented-manifest-id",
            "sources": [],
        }))
        self.assertNotEqual(submitted["detail"], "Context manifest not found")
        self.assertEqual(submitted["data"]["manifest_id"], manifest_id)

    def test_mcp_ready_manifest_is_committed_and_bound_to_run(self):
        from app.mcp.adapter import execute_tool

        run = AgentRun(project_id="p1", source="mcp", title="writing")
        self.db.add(run)
        self.db.commit()

        result = asyncio.run(execute_tool(
            self.db,
            "p1",
            "prepare_task_context",
            {
                "task_type": "writing",
                "run_id": run.id,
                "execution_route": "external_mcp",
                "arguments": {"outline_node_id": "o1"},
            },
            allowed_tiers={"readonly"},
        ))
        self.assertFalse(result.is_error)
        payload = json.loads(result.content[0]["text"])
        self.assertEqual(payload["status"], "ready")
        manifest_id = payload["data"]["context_manifest_id"]

        # Expire the current identity map and verify both records from the
        # committed database state, matching a later MCP process/tool call.
        self.db.expire_all()
        persisted = self.db.query(ContextManifest).filter(ContextManifest.id == manifest_id).first()
        rebound_run = self.db.query(AgentRun).filter(AgentRun.id == run.id).first()
        self.assertIsNotNone(persisted)
        self.assertEqual(rebound_run.context_manifest_id, manifest_id)

if __name__ == "__main__":
    unittest.main()
