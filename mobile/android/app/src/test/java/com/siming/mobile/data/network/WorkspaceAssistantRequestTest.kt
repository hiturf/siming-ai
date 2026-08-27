package com.siming.mobile.data.network

import kotlin.test.Test
import kotlin.test.assertEquals
import kotlinx.serialization.encodeToString
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.put

class WorkspaceAssistantRequestTest {
    @Test
    fun `mobile assistant continues canonical conversation with explicit history`() {
        val encoded = Json.encodeToString(
            WorkspaceAssistantRequest(
                message = "继续检查",
                conversationId = "conversation-1",
                history = listOf(buildJsonObject {
                    put("role", "assistant")
                    put("content", "上一轮结论")
                }),
            ),
        )
        val root = Json.parseToJsonElement(encoded).jsonObject
        assertEquals("conversation-1", root.getValue("conversation_id").jsonPrimitive.content)
        assertEquals("上一轮结论", root.getValue("history").jsonArray.single()
            .jsonObject.getValue("content").jsonPrimitive.content)
    }
}
