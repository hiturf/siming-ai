package com.siming.mobile.ui

import com.siming.mobile.data.local.ReplicaEntity
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class ReferenceAssistantWorkspaceTest {
    @Test
    fun `character summary prefers live goal over conflict and profile text`() {
        val character = replica(
            "character",
            "c1",
            "{\"name\":\"陆糖\",\"current_goal\":\"查清病毒来源\",\"active_conflict\":\"不能暴露真实来历\",\"personality\":\"冷静\"}",
        )
        assertEquals("查清病毒来源", characterPrimarySummary(character))
    }

    @Test
    fun `character tracking defaults on and respects explicit false`() {
        assertTrue(characterTracked(replica("character", "c1", "{\"name\":\"甲\"}")))
        assertFalse(characterTracked(replica("character", "c2", "{\"name\":\"乙\",\"is_evolution_tracked\":false}")))
    }

    @Test
    fun `mobile labels hide backend enum vocabulary`() {
        assertEquals("主角", characterRoleLabel("protagonist"))
        assertEquals("力量体系", worldDimensionLabel("power_system"))
        assertEquals("文化", worldDimensionLabel(""))
    }

    @Test
    fun `reference list and snippet are compact for mobile cards`() {
        assertEquals("剑术 · 阵法 · 炼丹", compactReferenceList("[\"剑术\", \"阵法\", \"炼丹\"]"))
        assertEquals("天地 玄黄 宇宙…", referenceSnippet("天地  玄黄\n宇宙洪荒", 8))
    }

    @Test
    fun `assistant quick actions only provide user messages without app routing`() {
        assertTrue(assistantQuickActions.any { it.label == "续写下一章" && "下一章" in it.prompt })
        assertTrue(assistantQuickActions.any { it.label == "检查世界观冲突" && "世界观" in it.prompt })
    }

    private fun replica(entityType: String, entityId: String, payload: String) = ReplicaEntity(
        key = ReplicaEntity.key("p1", entityType, entityId),
        projectId = "p1",
        entityType = entityType,
        entityId = entityId,
        revision = 1,
        operation = "upsert",
        payloadJson = payload,
        contentHash = "hash-$entityId",
        serverModifiedAt = "2026-08-19T00:00:00Z",
    )
}
