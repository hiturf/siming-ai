package com.siming.mobile.ui

import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertTrue

class AboutWorkspaceTest {
    @Test
    fun `about page keeps the same three author promises as desktop`() {
        assertEquals(
            listOf("作品属于作者", "事实先于生成", "边界必须透明"),
            mobileAboutPrinciples.map { it.title },
        )
    }

    @Test
    fun `official links and community details use canonical project values`() {
        assertEquals("https://github.com/teangtang1122/siming-ai", SIMING_REPOSITORY_URL)
        assertTrue(SIMING_RELEASES_URL.endsWith("/releases"))
        assertTrue(SIMING_ISSUES_URL.endsWith("/issues/new/choose"))
        assertEquals("814283606", SIMING_QQ_GROUP)
    }

    @Test
    fun `version label matches desktop presentation`() {
        assertEquals("v3.3.0", mobileAboutVersionLabel("3.3.0"))
    }
}
