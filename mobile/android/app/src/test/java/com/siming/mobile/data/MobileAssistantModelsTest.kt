package com.siming.mobile.data

import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertNotNull
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put

class MobileAssistantModelsTest {
    @Test
    fun `chapter tool result becomes an editor draft instead of assistant text`() {
        val draft = MobilePendingChapterDraft.fromJson(
            "project-1",
            buildJsonObject {
                put("draft_id", "draft-1")
                put("title", "雨夜追踪")
                put("content", "雨幕压住了街灯。")
                put("outline_node_id", "outline-1")
                put("draft_status", "pending")
                put("context_snapshot", buildJsonObject {
                    put("context_manifest_id", "manifest-1")
                    put("execution_route", "android_standalone")
                })
            },
        )

        assertNotNull(draft)
        assertEquals("draft-1", draft.draftId)
        assertEquals("雨幕压住了街灯。", draft.content)
        assertEquals("manifest-1", draft.contextManifestId)
        assertEquals("android_standalone", draft.executionRoute)
    }
}
