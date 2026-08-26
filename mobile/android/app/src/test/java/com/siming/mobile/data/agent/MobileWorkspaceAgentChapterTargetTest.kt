package com.siming.mobile.data.agent

import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertNull
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put

class MobileWorkspaceAgentChapterTargetTest {
    @Test
    fun `saved chapter outline cannot become a new ai draft target`() {
        val chapters = listOf(buildJsonObject {
            put("id", "chapter-1")
            put("outline_node_id", "outline-1")
        })

        assertEquals("chapter-1", existingMobileChapterIdForOutline(chapters, "outline-1"))
        assertNull(existingMobileChapterIdForOutline(chapters, "outline-2"))
    }
}
