package com.siming.mobile.data.creation

import java.time.Instant
import java.util.UUID
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.booleanOrNull
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.put

/** Canonical, atomic conversation record shared by standalone and gateway UI paths. */
internal object CreationAgentTurnRecords {
    const val SCHEMA = "creation_agent_turn.v1"
    const val STORAGE_KEY = "agent_turns"
    private const val GATEWAY_CONVERSATION_KEY = "agent_conversation_id"
    private const val MAX_STORED_TURNS = 20
    private const val MAX_REPLAY_TURNS = 6

    fun turns(session: JsonObject): List<JsonObject> =
        (session.objectValue("draft")[STORAGE_KEY] as? JsonArray)
            .orEmpty()
            .mapNotNull { it as? JsonObject }

    fun gatewayConversationId(session: JsonObject): String =
        session.objectValue("draft").string(GATEWAY_CONVERSATION_KEY)

    /** Keep local routing/conversation storage out of the business snapshot sent to the model. */
    fun agentVisibleDraft(session: JsonObject): JsonObject = JsonObject(
        session.objectValue("draft").filterKeys { it !in INTERNAL_DRAFT_KEYS },
    )

    fun pending(userContent: String): JsonObject = buildJsonObject {
        put("schema", SCHEMA)
        put("id", UUID.randomUUID().toString())
        put("created_at", Instant.now().toString())
        put("updated_at", Instant.now().toString())
        put("status", "running")
        put("user_content", userContent)
        put("reply", "")
        put("replayable", false)
        put("model_messages", JsonArray(emptyList()))
        put("tool_results", JsonArray(emptyList()))
    }

    fun complete(
        pending: JsonObject,
        reply: String,
        modelMessages: JsonArray,
        toolResults: JsonArray,
        replayable: Boolean,
        status: String = "completed",
        executionRoute: String,
        createdProjectId: String? = null,
        progressEvents: JsonArray = JsonArray(emptyList()),
        promptMetrics: JsonArray = JsonArray(emptyList()),
    ): JsonObject = JsonObject(pending.toMutableMap().apply {
        put("updated_at", JsonPrimitive(Instant.now().toString()))
        put("status", JsonPrimitive(status))
        put("reply", JsonPrimitive(reply))
        put("replayable", JsonPrimitive(replayable && status == "completed"))
        put("model_messages", modelMessages)
        put("tool_results", toolResults)
        put("execution_route", JsonPrimitive(executionRoute))
        put("progress_events", progressEvents)
        put("prompt_metrics", promptMetrics)
        createdProjectId?.takeIf(String::isNotBlank)?.let {
            put("created_project_id", JsonPrimitive(it))
        }
    })

    fun fail(pending: JsonObject, detail: String): JsonObject = complete(
        pending = pending,
        reply = detail.ifBlank { "本轮立项处理失败" },
        modelMessages = JsonArray(emptyList()),
        toolResults = JsonArray(emptyList()),
        replayable = false,
        status = "error",
        executionRoute = "error",
    )

    fun replace(turns: List<JsonObject>, replacement: JsonObject): List<JsonObject> {
        val id = replacement.string("id")
        val found = turns.any { it.string("id") == id }
        val updated = if (found) {
            turns.map { if (it.string("id") == id) replacement else it }
        } else {
            turns + replacement
        }
        return updated.takeLast(MAX_STORED_TURNS)
    }

    fun withTurns(
        session: JsonObject,
        turns: List<JsonObject>,
        gatewayConversationId: String? = null,
    ): JsonObject {
        val draft = session.objectValue("draft").toMutableMap()
        draft.remove("agent_history")
        draft[STORAGE_KEY] = JsonArray(turns.takeLast(MAX_STORED_TURNS))
        gatewayConversationId?.takeIf(String::isNotBlank)?.let {
            draft[GATEWAY_CONVERSATION_KEY] = JsonPrimitive(it)
        }
        return JsonObject(session.toMutableMap().apply { put("draft", JsonObject(draft)) })
    }

    fun displayMessages(session: JsonObject): List<JsonObject> = turns(session).flatMap { turn ->
        buildList {
            val turnId = turn.string("id")
            val createdAt = turn.string("created_at")
            val userContent = turn.string("user_content")
            if (userContent.isNotBlank()) add(displayMessage("$turnId:user", "user", userContent, createdAt))
            val reply = turn.string("reply")
            if (reply.isNotBlank()) add(displayMessage(
                "$turnId:assistant",
                "assistant",
                reply,
                turn.string("updated_at"),
                turn["progress_events"] as? JsonArray ?: JsonArray(emptyList()),
            ))
        }
    }

    fun replayMessages(turns: List<JsonObject>): List<JsonObject> = turns
        .mapNotNull { turn ->
            if (turn.string("schema") != SCHEMA || turn.string("status") != "completed") return@mapNotNull null
            if ((turn["replayable"] as? JsonPrimitive)?.booleanOrNull != true) return@mapNotNull null
            withoutControlRounds(validatedMessages(turn["model_messages"]))
                .takeIf(List<JsonObject>::isNotEmpty)
        }
        .takeLast(MAX_REPLAY_TURNS)
        .flatten()

    /** One-time data migration; old text bubbles never enter model replay. */
    fun migrateLegacyHistory(session: JsonObject): JsonObject {
        val draft = session.objectValue("draft")
        if (draft[STORAGE_KEY] is JsonArray) {
            if (draft["agent_history"] !is JsonArray) return session
            val cleaned = draft.toMutableMap().apply { remove("agent_history") }
            return JsonObject(session.toMutableMap().apply { put("draft", JsonObject(cleaned)) })
        }
        if (draft["agent_history"] !is JsonArray) return session
        val legacy = (draft["agent_history"] as JsonArray).mapNotNull { it as? JsonObject }
        val migrated = mutableListOf<JsonObject>()
        var pendingUser: JsonObject? = null
        legacy.forEach { message ->
            when (message.string("role")) {
                "user" -> {
                    pendingUser?.let { migrated += migratedTurn(it, null) }
                    pendingUser = message
                }
                "assistant" -> {
                    pendingUser?.let { migrated += migratedTurn(it, message) }
                    pendingUser = null
                }
            }
        }
        pendingUser?.let { migrated += migratedTurn(it, null) }
        return withTurns(session, migrated)
    }

    private fun migratedTurn(user: JsonObject, assistant: JsonObject?): JsonObject = buildJsonObject {
        put("schema", SCHEMA)
        put("id", UUID.randomUUID().toString())
        put("created_at", user.string("created_at").ifBlank { Instant.now().toString() })
        put("updated_at", assistant?.string("created_at").orEmpty().ifBlank { Instant.now().toString() })
        put("status", if (assistant == null) "error" else "completed")
        put("user_content", user.string("content"))
        put("reply", assistant?.string("content").orEmpty())
        put("replayable", false)
        put("model_messages", JsonArray(emptyList()))
        put("tool_results", JsonArray(emptyList()))
        put("execution_route", "migrated")
    }

    private fun validatedMessages(value: JsonElement?): List<JsonObject> {
        val messages = (value as? JsonArray).orEmpty().mapNotNull { it as? JsonObject }
        if (messages.isEmpty()) return emptyList()
        val pendingToolIds = mutableSetOf<String>()
        var finalAssistantSeen = false
        messages.forEachIndexed { index, message ->
            if (finalAssistantSeen) return emptyList()
            val role = message.string("role")
            if (index == 0) {
                if (role != "user" || message.string("content").isBlank()) return emptyList()
                return@forEachIndexed
            }
            when (role) {
                "assistant" -> {
                    if (pendingToolIds.isNotEmpty()) return emptyList()
                    val calls = (message["tool_calls"] as? JsonArray).orEmpty().mapNotNull { it as? JsonObject }
                    if (calls.isEmpty()) {
                        if (message.string("content").isBlank()) return emptyList()
                        finalAssistantSeen = true
                    } else {
                        val ids = calls.map { it.string("id") }
                        if (ids.any(String::isBlank) || ids.distinct().size != ids.size) return emptyList()
                        if (calls.any { (it["function"] as? JsonObject)?.string("name").isNullOrBlank() }) return emptyList()
                        pendingToolIds += ids
                    }
                }
                "tool" -> {
                    val callId = message.string("tool_call_id")
                    if (!pendingToolIds.remove(callId)) return emptyList()
                }
                else -> return emptyList()
            }
        }
        return if (pendingToolIds.isEmpty() && finalAssistantSeen) messages else emptyList()
    }

    private fun withoutControlRounds(messages: List<JsonObject>): List<JsonObject> {
        val controlCallIds = messages.flatMap { message ->
            ((message["tool_calls"] as? JsonArray).orEmpty())
                .mapNotNull { it as? JsonObject }
                .filter { call ->
                    ((call["function"] as? JsonObject)?.string("name")) == "set_tool_categories"
                }
                .map { it.string("id") }
        }.filter(String::isNotBlank).toSet()
        if (controlCallIds.isEmpty()) return messages
        return messages.mapNotNull { message ->
            when (message.string("role")) {
                "tool" -> message.takeUnless { it.string("tool_call_id") in controlCallIds }
                "assistant" -> {
                    val calls = (message["tool_calls"] as? JsonArray).orEmpty()
                        .mapNotNull { it as? JsonObject }
                        .filterNot { it.string("id") in controlCallIds }
                    if (calls.isEmpty() && message.string("content").isBlank()) null
                    else JsonObject(message.toMutableMap().apply {
                        if (calls.isEmpty()) remove("tool_calls") else put("tool_calls", JsonArray(calls))
                    })
                }
                else -> message
            }
        }
    }

    private fun displayMessage(
        id: String,
        role: String,
        content: String,
        createdAt: String,
        progressEvents: JsonArray = JsonArray(emptyList()),
    ) = buildJsonObject {
        put("id", id)
        put("role", role)
        put("content", content)
        put("created_at", createdAt)
        if (progressEvents.isNotEmpty()) put("progress_events", progressEvents)
    }

    private fun JsonObject.objectValue(name: String): JsonObject = get(name) as? JsonObject ?: JsonObject(emptyMap())
    private fun JsonObject.string(name: String): String = (get(name) as? JsonPrimitive)?.contentOrNull.orEmpty()

    private val INTERNAL_DRAFT_KEYS = setOf(
        STORAGE_KEY,
        GATEWAY_CONVERSATION_KEY,
        "agent_history",
        "execution_route",
        "execution_host",
    )
}
