package com.siming.mobile.data.agent

import java.nio.file.Files
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertTrue
import kotlinx.coroutines.runBlocking

class MobileAssistantConversationStoreTest {
    @Test
    fun `standalone transcript and tool log survive a new store instance`() = runBlocking {
        val directory = Files.createTempDirectory("siming-mobile-assistant").toFile()
        try {
            val firstStore = MobileAssistantConversationStore(directory)
            val first = firstStore.beginTurn("project-1", null, "检查人物动机")
            firstStore.finishTurn(
                projectId = "project-1",
                conversationId = first.conversationId,
                content = "已检查主要角色。",
                status = "completed",
                toolLogs = listOf("已读取 3 个角色"),
            )

            val restored = MobileAssistantConversationStore(directory)
            val conversations = restored.conversations("project-1")
            val messages = restored.messages("project-1", first.conversationId)

            assertEquals(1, conversations.size)
            assertEquals(2, conversations.single().messageCount)
            assertEquals(listOf("user", "assistant"), messages.map { it.role })
            assertEquals(listOf("已读取 3 个角色"), messages.last().toolLogs)

            val next = restored.beginTurn("project-1", first.conversationId, "继续")
            assertEquals(2, next.history.size)
            assertTrue(next.history.last().content.contains("已检查"))
        } finally {
            directory.deleteRecursively()
        }
    }
}
