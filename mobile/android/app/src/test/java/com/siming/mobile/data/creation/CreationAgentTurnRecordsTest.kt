package com.siming.mobile.data.creation

import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertNull
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonArray
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.put

class CreationAgentTurnRecordsTest {
    @Test
    fun `replay preserves a complete assistant tool round atomically`() {
        val pending = CreationAgentTurnRecords.pending("把门派名改成归墟宗")
        val modelMessages = buildJsonArray {
            add(message("user", "把门派名改成归墟宗"))
            add(buildJsonObject {
                put("role", "assistant")
                put("content", "")
                put("tool_calls", buildJsonArray {
                    add(toolCall("call-read", "get_creation_snapshot"))
                })
            })
            add(buildJsonObject {
                put("role", "tool")
                put("tool_call_id", "call-read")
                put("content", "{\"status\":\"ok\"}")
            })
            add(message("assistant", "已根据工具结果完成处理。"))
        }
        val turn = CreationAgentTurnRecords.complete(
            pending = pending,
            reply = "已根据工具结果完成处理。",
            modelMessages = modelMessages,
            toolResults = JsonArray(emptyList()),
            replayable = true,
            executionRoute = "device",
        )

        val replay = CreationAgentTurnRecords.replayMessages(listOf(turn))

        assertEquals(listOf("user", "assistant", "tool", "assistant"), replay.map { it.string("role") })
        assertEquals("call-read", replay[2].string("tool_call_id"))
    }

    @Test
    fun `replay excludes the whole turn when a tool result is missing`() {
        val pending = CreationAgentTurnRecords.pending("继续")
        val corrupt = CreationAgentTurnRecords.complete(
            pending = pending,
            reply = "无法确认",
            modelMessages = buildJsonArray {
                add(message("user", "继续"))
                add(buildJsonObject {
                    put("role", "assistant")
                    put("content", "")
                    put("tool_calls", buildJsonArray { add(toolCall("call-1", "get_creation_snapshot")) })
                })
                add(message("assistant", "无法确认"))
            },
            toolResults = JsonArray(emptyList()),
            replayable = true,
            executionRoute = "device",
        )

        assertEquals(emptyList(), CreationAgentTurnRecords.replayMessages(listOf(corrupt)))
    }

    @Test
    fun `legacy bubbles migrate once for display but never enter model replay`() {
        val legacySession = buildJsonObject {
            put("id", "session-1")
            put("draft", buildJsonObject {
                put("agent_history", buildJsonArray {
                    add(buildJsonObject {
                        put("id", "user-old")
                        put("role", "user")
                        put("content", "旧问题")
                    })
                    add(buildJsonObject {
                        put("id", "assistant-old")
                        put("role", "assistant")
                        put("content", "旧回答")
                    })
                })
            })
        }

        val migrated = CreationAgentTurnRecords.migrateLegacyHistory(legacySession)
        val draft = migrated["draft"] as JsonObject

        assertNull(draft["agent_history"])
        assertEquals(1, CreationAgentTurnRecords.turns(migrated).size)
        assertEquals(listOf("旧问题", "旧回答"), CreationAgentTurnRecords.displayMessages(migrated).map { it.string("content") })
        assertFalse(CreationAgentTurnRecords.turns(migrated).single().boolean("replayable"))
        assertEquals(emptyList(), CreationAgentTurnRecords.replayMessages(CreationAgentTurnRecords.turns(migrated)))
    }

    private fun message(role: String, content: String) = buildJsonObject {
        put("role", role)
        put("content", content)
    }

    private fun toolCall(id: String, name: String) = buildJsonObject {
        put("id", id)
        put("type", "function")
        put("function", buildJsonObject {
            put("name", name)
            put("arguments", "{}")
        })
    }

    private fun JsonObject.string(name: String): String = (get(name) as? JsonPrimitive)?.contentOrNull.orEmpty()
    private fun JsonObject.boolean(name: String): Boolean = string(name).toBooleanStrictOrNull() ?: false
}
