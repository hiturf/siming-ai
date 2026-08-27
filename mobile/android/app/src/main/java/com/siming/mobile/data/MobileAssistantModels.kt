package com.siming.mobile.data

import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.contentOrNull

data class MobilePendingChapterDraft(
    val draftId: String,
    val projectId: String,
    val title: String,
    val content: String,
    val outlineNodeId: String? = null,
    val contextManifestId: String? = null,
    val status: String = "pending",
    val executionRoute: String = "gateway",
) {
    val generating: Boolean get() = status == "generating"

    companion object {
        fun fromJson(projectId: String, value: JsonObject): MobilePendingChapterDraft? {
            val id = value.string("draft_id").ifBlank { value.string("content_ref") }
            if (id.isBlank()) return null
            return MobilePendingChapterDraft(
                draftId = id,
                projectId = value.string("project_id").ifBlank { projectId },
                title = value.string("title").ifBlank { "AI 生成章节" },
                content = value.string("content"),
                outlineNodeId = value.string("outline_node_id").ifBlank { null },
                contextManifestId = value.string("context_manifest_id").ifBlank {
                    (value["context_snapshot"] as? JsonObject)?.string("context_manifest_id").orEmpty()
                }.ifBlank { null },
                status = value.string("draft_status").ifBlank { "pending" },
                executionRoute = value.string("execution_route").ifBlank {
                    (value["context_snapshot"] as? JsonObject)?.string("execution_route").orEmpty()
                }.ifBlank { "gateway" },
            )
        }
    }
}

data class MobileAssistantConversation(
    val id: String,
    val title: String,
    val messageCount: Int = 0,
    val updatedAt: String = "",
)

data class MobileAssistantMessage(
    val id: String,
    val role: String,
    val content: String,
    val status: String = "completed",
    val createdAt: String = "",
    val toolLogs: List<String> = emptyList(),
)

private fun JsonObject.string(name: String): String =
    (get(name) as? JsonPrimitive)?.contentOrNull.orEmpty()
