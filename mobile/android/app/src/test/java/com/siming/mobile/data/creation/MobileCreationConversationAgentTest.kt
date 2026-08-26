package com.siming.mobile.data.creation

import com.siming.mobile.data.network.DirectApiClient
import com.siming.mobile.data.network.DirectApiConfig
import java.io.File
import java.util.concurrent.atomic.AtomicInteger
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFailsWith
import kotlin.test.assertFalse
import kotlin.test.assertTrue
import kotlinx.coroutines.runBlocking
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.put
import okhttp3.mockwebserver.Dispatcher
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.mockwebserver.RecordedRequest

class MobileCreationConversationAgentTest {
    @Test
    fun `mobile replay removes controller calls and results as one protocol pair`() {
        val turn = buildJsonObject {
            put("schema", CreationAgentTurnRecords.SCHEMA)
            put("status", "completed")
            put("replayable", true)
            put("model_messages", JsonArray(listOf(
                buildJsonObject { put("role", "user"); put("content", "修改主角") },
                buildJsonObject {
                    put("role", "assistant")
                    put("content", "")
                    put("tool_calls", JsonArray(listOf(
                        buildJsonObject {
                            put("id", "call-categories")
                            put("function", buildJsonObject {
                                put("name", "set_tool_categories")
                                put("arguments", "{\"enabled_categories\":[\"creation_data\"]}")
                            })
                        },
                    )))
                },
                buildJsonObject { put("role", "tool"); put("tool_call_id", "call-categories"); put("content", "{\"status\":\"ok\"}") },
                buildJsonObject {
                    put("role", "assistant")
                    put("content", "")
                    put("tool_calls", JsonArray(listOf(buildJsonObject {
                        put("id", "call-snapshot")
                        put("function", buildJsonObject {
                            put("name", "get_creation_snapshot")
                            put("arguments", "{}")
                        })
                    })))
                },
                buildJsonObject { put("role", "tool"); put("tool_call_id", "call-snapshot"); put("content", "{\"status\":\"ok\"}") },
                buildJsonObject { put("role", "assistant"); put("content", "已读取主角资料。") },
            )))
        }

        val replay = CreationAgentTurnRecords.replayMessages(listOf(turn))
        val wire = replay.toString()

        assertFalse("set_tool_categories" in wire)
        assertFalse("call-categories" in wire)
        assertTrue("get_creation_snapshot" in wire)
        assertEquals(listOf("user", "assistant", "tool", "assistant"), replay.map { it.string("role") })
    }

    @Test
    fun `standalone agent selects categories before reading and preserves complete rounds`() {
        val requests = AtomicInteger()
        withServer(object : Dispatcher() {
            override fun dispatch(request: RecordedRequest): MockResponse {
                val body = Json.parseToJsonElement(request.body.readUtf8()).jsonObject
                return when (requests.getAndIncrement()) {
                    0 -> {
                        assertEquals("required", body.getValue("tool_choice").jsonPrimitive.content)
                        jsonResponse(
                            """{"choices":[{"message":{"role":"assistant","content":null,"tool_calls":[{"id":"call-categories","type":"function","function":{"name":"set_tool_categories","arguments":"{\"enabled_categories\":[\"creation_data\"]}"}}]}}],"usage":{"prompt_tokens":88}}""",
                        )
                    }
                    1 -> {
                        assertEquals("auto", body.getValue("tool_choice").jsonPrimitive.content)
                        jsonResponse(
                            """{"choices":[{"message":{"role":"assistant","content":null,"tool_calls":[{"id":"call-read","type":"function","function":{"name":"get_creation_snapshot","arguments":"{}"}}]}}],"usage":{"prompt_tokens":100}}""",
                        )
                    }
                    else -> {
                        assertEquals("auto", body.getValue("tool_choice").jsonPrimitive.content)
                        val messages = body.getValue("messages").jsonArray.map { it.jsonObject }
                        assertTrue(messages.any { (it["tool_calls"] as? JsonArray)?.isNotEmpty() == true })
                        val toolMessage = messages.first {
                            it.string("role") == "tool" && it.string("tool_call_id") == "call-read"
                        }
                        val toolResult = Json.parseToJsonElement(toolMessage.string("content")).jsonObject
                        val visibleDraft = toolResult.getValue("data").jsonObject.getValue("draft").jsonObject
                        assertFalse("agent_turns" in visibleDraft)
                        assertFalse("agent_conversation_id" in visibleDraft)
                        assertFalse("execution_route" in visibleDraft)
                        assertFalse("execution_host" in visibleDraft)
                        jsonResponse(
                            """{"choices":[{"message":{"role":"assistant","content":"已读取当前立项资料，没有修改数据。"}}],"usage":{"prompt_tokens":144}}""",
                        )
                    }
                }
            }
        }) { server ->
            val result = runBlocking { agent().run(
                source = session(),
                message = "先看看当前资料",
                turns = emptyList(),
                config = config(server),
            ) }

            assertEquals(3, requests.get())
            assertEquals("completed", result.status)
            assertTrue(result.replayable)
            assertEquals("已读取当前立项资料，没有修改数据。", result.reply)
            assertEquals(
                listOf("user", "assistant", "tool", "assistant", "tool", "assistant"),
                result.modelMessages.map { (it as JsonObject).string("role") },
            )
            assertEquals(88, result.promptMetrics[0].jsonObject.getValue("prompt_tokens").jsonPrimitive.content.toInt())
            assertEquals(100, result.promptMetrics[1].jsonObject.getValue("prompt_tokens").jsonPrimitive.content.toInt())
            assertEquals(144, result.promptMetrics[2].jsonObject.getValue("prompt_tokens").jsonPrimitive.content.toInt())
        }
    }

    @Test
    fun `standalone agent rejects text before selecting tool categories`() {
        withServer(object : Dispatcher() {
            override fun dispatch(request: RecordedRequest): MockResponse {
                val body = Json.parseToJsonElement(request.body.readUtf8()).jsonObject
                assertEquals("required", body.getValue("tool_choice").jsonPrimitive.content)
                return jsonResponse(
                    """{"choices":[{"message":{"role":"assistant","content":"我已经读取并保存了设定。"}}]}""",
                )
            }
        }) { server ->
            val error = assertFailsWith<IllegalStateException> {
                runBlocking { agent().run(
                    source = session(),
                    message = "加入一个新设定",
                    turns = emptyList(),
                    config = config(server),
                ) }
            }
            assertTrue(error.message.orEmpty().contains("set_tool_categories"))
        }
    }

    @Test
    fun `standalone agent persists only one successful write per user message`() {
        val requests = AtomicInteger()
        val persisted = AtomicInteger()
        withServer(object : Dispatcher() {
            override fun dispatch(request: RecordedRequest): MockResponse {
                val body = Json.parseToJsonElement(request.body.readUtf8()).jsonObject
                return when (requests.getAndIncrement()) {
                    0 -> jsonResponse(
                        """{"choices":[{"message":{"role":"assistant","content":null,"tool_calls":[{"id":"call-categories","type":"function","function":{"name":"set_tool_categories","arguments":"{\"enabled_categories\":[\"creation_data\"]}"}}]}}]}""",
                    )
                    1 -> jsonResponse(
                        """{"choices":[{"message":{"role":"assistant","content":null,"tool_calls":[{"id":"call-write-one","type":"function","function":{"name":"patch_creation_session","arguments":"{\"changes\":{\"genre\":\"玄幻\"}}"}},{"id":"call-write-two","type":"function","function":{"name":"patch_creation_session","arguments":"{\"changes\":{\"target_chapters\":1000}}"}}]}}]}""",
                    )
                    else -> {
                        assertTrue(body.getValue("tools").jsonArray.isEmpty())
                        assertFalse("tool_choice" in body)
                        jsonResponse(
                            """{"choices":[{"message":{"role":"assistant","content":"本轮只记录了题材。下一步想补充什么？"}}]}""",
                        )
                    }
                }
            }
        }) { server ->
            val client = DirectApiClient(allowCleartextForTests = true, retryDelaysMillis = emptyList())
            val contract = contractJson()
            val standalone = MobileCreationConversationAgent(
                contract = PcCreationAgentContract(contract),
                stageAgent = MobileCreationAgent(contract, client),
                directApi = client,
                persistSession = { persisted.incrementAndGet() },
                finalizeSession = { source -> source to "project-1" },
            )
            val result = runBlocking { standalone.run(
                source = session(),
                message = "继续",
                turns = emptyList(),
                config = config(server),
            ) }

            assertEquals(3, requests.get())
            assertEquals(1, persisted.get())
            assertEquals(2, result.session.getValue("revision").jsonPrimitive.content.toInt())
            val businessResults = result.toolResults.map { it.jsonObject }
                .filter { it.string("tool") == "patch_creation_session" }
            assertEquals(listOf("ok", "denied"), businessResults.map { it.string("status") })
            assertEquals("本轮只记录了题材。下一步想补充什么？", result.reply)
        }
    }

    private fun agent(): MobileCreationConversationAgent {
        val client = DirectApiClient(allowCleartextForTests = true, retryDelaysMillis = emptyList())
        val contract = contractJson()
        return MobileCreationConversationAgent(
            contract = PcCreationAgentContract(contract),
            stageAgent = MobileCreationAgent(contract, client),
            directApi = client,
            persistSession = {},
            finalizeSession = { source -> source to "project-1" },
        )
    }

    private fun config(server: MockWebServer) = DirectApiConfig(
        displayName = "test",
        baseUrl = server.url("/").toString(),
        apiKey = "secret",
        model = "test-model",
        protocol = DirectApiConfig.PROTOCOL_CHAT_COMPLETIONS,
    )

    private fun session() = buildJsonObject {
        put("id", "session-1")
        put("revision", 1)
        put("display_title", "测试立项")
        put("draft", buildJsonObject {
            put("form", buildJsonObject {})
            put("stages", buildJsonObject {})
            put("agent_turns", JsonArray(listOf(buildJsonObject {
                put("schema", CreationAgentTurnRecords.SCHEMA)
                put("user_content", "不应递归进入工具快照")
            })))
            put("agent_conversation_id", "conversation-1")
            put("execution_route", "mobile")
            put("execution_host", "device")
        })
    }

    private fun contractJson(): String {
        val candidates = listOf(
            File("src/main/assets/pc_workspace_prompt_contract.json"),
            File("app/src/main/assets/pc_workspace_prompt_contract.json"),
        )
        return candidates.first(File::isFile).readText(Charsets.UTF_8)
    }

    private fun jsonResponse(body: String) = MockResponse()
        .setResponseCode(200)
        .setHeader("Content-Type", "application/json")
        .setBody(body)

    private fun withServer(dispatcher: Dispatcher, block: (MockWebServer) -> Unit) {
        MockWebServer().use { server ->
            server.dispatcher = dispatcher
            server.start()
            block(server)
        }
    }

    private fun JsonObject.string(name: String): String =
        (get(name) as? JsonPrimitive)?.contentOrNull.orEmpty()
}
