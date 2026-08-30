package com.siming.mobile.ui

import com.siming.mobile.data.mobileAssistantContextFailureEvent
import com.siming.mobile.data.agent.MobileConversationContextErrorCode
import com.siming.mobile.data.agent.MobileConversationContextException
import com.siming.mobile.data.agent.mobileCapacityBoundTaskConfig
import com.siming.mobile.data.local.ReplicaEntity
import com.siming.mobile.data.network.DirectApiConfig
import kotlinx.serialization.json.JsonObject
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

    @Test
    fun `unknown direct model capacity exposes the explicit configuration action`() {
        assertTrue(
            requiresDirectContextCapacityConfiguration(
                MobileAssistantContextState(
                    status = "failed",
                    errorCode = "conversation_capacity_unknown",
                ),
            ),
        )
        assertFalse(
            requiresDirectContextCapacityConfiguration(
                MobileAssistantContextState(
                    status = "failed",
                    errorCode = "conversation_checkpoint_failed",
                ),
            ),
        )
    }

    @Test
    fun `assistant task model override emits typed capacity event for configuration action`() {
        val config = DirectApiConfig(
            displayName = "test",
            baseUrl = "https://example.test/v1",
            apiKey = "secret",
            model = "general-model",
            taskModels = mapOf(DirectApiConfig.TASK_ASSISTANT to "assistant-model"),
            contextWindowTokens = 128_000,
        )
        val failure = try {
            mobileCapacityBoundTaskConfig(config, DirectApiConfig.TASK_ASSISTANT)
            error("assistant task-model override should fail closed")
        } catch (error: MobileConversationContextException) {
            error
        }

        val event = mobileAssistantContextFailureEvent(
            error = failure,
            conversationId = "conversation-1",
            model = config.modelForTask(DirectApiConfig.TASK_ASSISTANT),
        )
        assertEquals("conversation_context", event["type"]?.toString()?.trim('"'))
        val contextState = mobileAssistantContextStateFromJson(
            event["context_state"] as JsonObject,
        )
        assertEquals("failed", contextState.status)
        assertEquals(MobileConversationContextErrorCode.CAPACITY_UNKNOWN, contextState.errorCode)
        assertEquals("assistant-model", contextState.model)
        assertTrue(requiresDirectContextCapacityConfiguration(contextState))
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
