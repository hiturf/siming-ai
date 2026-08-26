package com.siming.mobile.data.creation

import kotlinx.serialization.json.JsonObject

data class CreationStartInput(
    val creationMode: String,
    val brief: String,
    val presetId: String = "free",
    val themeId: String = "",
    val authorOutline: String = "",
    val genre: String = "自由创作",
    val targetAudience: String = "成年大众",
    val platform: String = "暂不确定",
    val targetWords: Int = 600_000,
    val targetChapters: Int = 240,
    val worldTone: String = "",
    val storyStructure: String = "",
    val pacing: String = "",
    val writingStyle: String = "",
    val specialRequirements: List<String> = emptyList(),
    val avoid: List<String> = emptyList(),
    val lockedRequirements: List<String> = emptyList(),
)

enum class CreationExecutionRoute {
    Pc,
    MobileKey,
}

data class CreationProgress(
    val sessionId: String = "",
    val activity: String = "",
    val running: Boolean = false,
)

data class CreationAgentProgressEvent(
    val type: String,
    val message: String,
    val status: String = "running",
    val data: JsonObject = JsonObject(emptyMap()),
    val clientTurnId: String = "",
    val sequence: Long = 0,
)
