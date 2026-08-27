package com.siming.mobile.data.agent

import android.content.Context
import com.siming.mobile.data.MobileAssistantConversation
import com.siming.mobile.data.MobileAssistantMessage
import java.io.File
import java.time.Instant
import java.util.UUID
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonArray
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.put

internal data class MobileAssistantTurnContext(
    val conversationId: String,
    val history: List<MobileAssistantMessage>,
)

/** Durable standalone assistant transcript, separate from synchronized story data. */
internal class MobileAssistantConversationStore(
    private val directory: File,
    private val json: Json = Json { ignoreUnknownKeys = true },
) {
    constructor(context: Context) : this(
        File(context.applicationContext.filesDir, DIRECTORY_NAME),
    )

    private val mutex = Mutex()

    suspend fun conversations(projectId: String): List<MobileAssistantConversation> = read(projectId)
        .sortedByDescending(LocalConversation::updatedAt)
        .map { value ->
            MobileAssistantConversation(
                id = value.id,
                title = value.title,
                messageCount = value.messages.size,
                updatedAt = value.updatedAt,
            )
        }

    suspend fun messages(projectId: String, conversationId: String): List<MobileAssistantMessage> =
        read(projectId).firstOrNull { it.id == conversationId }?.messages.orEmpty()

    suspend fun beginTurn(
        projectId: String,
        conversationId: String?,
        prompt: String,
    ): MobileAssistantTurnContext = mutate(projectId) { conversations ->
        val now = Instant.now().toString()
        val index = conversationId?.let { id -> conversations.indexOfFirst { it.id == id } } ?: -1
        val current = if (index >= 0) {
            conversations[index]
        } else {
            LocalConversation(
                id = UUID.randomUUID().toString(),
                title = prompt.trim().replace(Regex("\\s+"), " ").take(36).ifBlank { "新对话" },
                createdAt = now,
                updatedAt = now,
                messages = emptyList(),
            )
        }
        val history = current.messages
        val updated = current.copy(
            updatedAt = now,
            messages = history + MobileAssistantMessage(
                id = UUID.randomUUID().toString(),
                role = "user",
                content = prompt,
                createdAt = now,
            ),
        )
        if (index >= 0) conversations[index] = updated else conversations += updated
        MobileAssistantTurnContext(updated.id, history)
    }

    suspend fun finishTurn(
        projectId: String,
        conversationId: String,
        content: String,
        status: String,
        toolLogs: List<String>,
    ) {
        mutate(projectId) { conversations ->
            val index = conversations.indexOfFirst { it.id == conversationId }
            if (index < 0) return@mutate Unit
            val now = Instant.now().toString()
            val current = conversations[index]
            conversations[index] = current.copy(
                updatedAt = now,
                messages = current.messages + MobileAssistantMessage(
                    id = UUID.randomUUID().toString(),
                    role = "assistant",
                    content = content,
                    status = status,
                    createdAt = now,
                    toolLogs = toolLogs,
                ),
            )
        }
    }

    private suspend fun read(projectId: String): List<LocalConversation> = withContext(Dispatchers.IO) {
        mutex.withLock { readLocked(projectId) }
    }

    private suspend fun <T> mutate(
        projectId: String,
        action: (MutableList<LocalConversation>) -> T,
    ): T = withContext(Dispatchers.IO) {
        mutex.withLock {
            val conversations = readLocked(projectId).toMutableList()
            val result = action(conversations)
            writeLocked(projectId, conversations.sortedByDescending(LocalConversation::updatedAt).take(MAX_CONVERSATIONS))
            result
        }
    }

    private fun readLocked(projectId: String): List<LocalConversation> {
        val target = file(projectId)
        if (!target.isFile) return emptyList()
        return runCatching {
            val root = json.parseToJsonElement(target.readText(Charsets.UTF_8)) as JsonObject
            (root["conversations"] as? JsonArray).orEmpty().mapNotNull { raw ->
                LocalConversation.fromJson(raw as? JsonObject ?: return@mapNotNull null)
            }
        }.getOrDefault(emptyList())
    }

    private fun writeLocked(projectId: String, conversations: List<LocalConversation>) {
        directory.mkdirs()
        val target = file(projectId)
        val temporary = File(directory, ".${target.name}.${UUID.randomUUID()}.tmp")
        temporary.writeText(buildJsonObject {
            put("schema_version", 1)
            put("conversations", buildJsonArray { conversations.forEach { add(it.toJson()) } })
        }.toString(), Charsets.UTF_8)
        if (!temporary.renameTo(target)) {
            target.writeText(temporary.readText(Charsets.UTF_8), Charsets.UTF_8)
            temporary.delete()
        }
    }

    private fun file(projectId: String): File {
        require(projectId.matches(PROJECT_ID_PATTERN)) { "无效的作品 ID" }
        return File(directory, "$projectId.json")
    }

    private data class LocalConversation(
        val id: String,
        val title: String,
        val createdAt: String,
        val updatedAt: String,
        val messages: List<MobileAssistantMessage>,
    ) {
        fun toJson(): JsonObject = buildJsonObject {
            put("id", id)
            put("title", title)
            put("created_at", createdAt)
            put("updated_at", updatedAt)
            put("messages", buildJsonArray {
                messages.takeLast(MAX_MESSAGES).forEach { message ->
                    add(buildJsonObject {
                        put("id", message.id)
                        put("role", message.role)
                        put("content", message.content)
                        put("status", message.status)
                        put("created_at", message.createdAt)
                        put("tool_logs", JsonArray(message.toolLogs.map(::JsonPrimitive)))
                    })
                }
            })
        }

        companion object {
            fun fromJson(root: JsonObject): LocalConversation? {
                val id = root.string("id")
                if (id.isBlank()) return null
                return LocalConversation(
                    id = id,
                    title = root.string("title").ifBlank { "新对话" },
                    createdAt = root.string("created_at"),
                    updatedAt = root.string("updated_at"),
                    messages = (root["messages"] as? JsonArray).orEmpty().mapNotNull { raw ->
                        val item = raw as? JsonObject ?: return@mapNotNull null
                        MobileAssistantMessage(
                            id = item.string("id").ifBlank { UUID.randomUUID().toString() },
                            role = item.string("role"),
                            content = item.string("content"),
                            status = item.string("status").ifBlank { "completed" },
                            createdAt = item.string("created_at"),
                            toolLogs = (item["tool_logs"] as? JsonArray).orEmpty().mapNotNull {
                                (it as? JsonPrimitive)?.contentOrNull
                            },
                        )
                    },
                )
            }
        }
    }

    companion object {
        private const val DIRECTORY_NAME = "mobile-assistant-conversations"
        private const val MAX_CONVERSATIONS = 40
        private const val MAX_MESSAGES = 200
        private val PROJECT_ID_PATTERN = Regex("[A-Za-z0-9._:-]{1,64}")
    }
}

private fun JsonObject.string(name: String): String =
    (get(name) as? JsonPrimitive)?.contentOrNull.orEmpty()
