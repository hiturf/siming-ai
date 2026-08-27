package com.siming.mobile.data.creation

import android.content.Context
import com.siming.mobile.data.network.DirectAgentToolCall
import com.siming.mobile.data.network.DirectApiClient
import com.siming.mobile.data.network.DirectApiConfig
import java.time.Instant
import kotlinx.coroutines.CancellationException
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonArray
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.intOrNull
import kotlinx.serialization.json.put

internal data class MobileCreationConversationResult(
    val session: JsonObject,
    val reply: String,
    val toolResults: JsonArray,
    val modelMessages: JsonArray,
    val replayable: Boolean,
    val status: String,
    val createdProjectId: String? = null,
    val promptMetrics: JsonArray = JsonArray(emptyList()),
)

/**
 * Standalone Android projection of backend/services/novel_creation_agent.py.
 *
 * The model gets the same build-generated system prompt and tool schemas as PC.
 * Storage is the only mobile-specific layer: tools mutate the local creation
 * session and the repository persists each successful write immediately.
 */
internal class MobileCreationConversationAgent(
    private val contract: PcCreationAgentContract,
    private val stageAgent: MobileCreationAgent,
    private val directApi: DirectApiClient,
    private val persistSession: suspend (JsonObject) -> Unit,
    private val finalizeSession: suspend (JsonObject) -> Pair<JsonObject, String>,
) {
    constructor(
        context: Context,
        stageAgent: MobileCreationAgent,
        directApi: DirectApiClient,
        persistSession: suspend (JsonObject) -> Unit,
        finalizeSession: suspend (JsonObject) -> Pair<JsonObject, String>,
    ) : this(
        PcCreationAgentContract(context.applicationContext),
        stageAgent,
        directApi,
        persistSession,
        finalizeSession,
    )

    suspend fun run(
        source: JsonObject,
        message: String,
        turns: List<JsonObject>,
        config: DirectApiConfig,
        onProgress: suspend (CreationAgentProgressEvent) -> Unit = {},
    ): MobileCreationConversationResult {
        require(message.isNotBlank()) { "请输入你想告诉 AI 的内容" }
        var working = source
        var createdProjectId: String? = null
        val toolResults = mutableListOf<JsonElement>()
        val turnProtocolMessages = mutableListOf<JsonElement>()
        val promptMetrics = mutableListOf<JsonElement>()
        val messages = mutableListOf<JsonObject>()
        messages += chatMessage("system", contract.systemPrompt(source.string("id")))
        messages += CreationAgentTurnRecords.replayMessages(turns)
        val userMessage = chatMessage("user", message)
        messages += userMessage

        var finalReply = ""
        var iteration = 0
        var activeCategories = emptyList<String>()
        var categorySelected = false
        var successfulWriteCount = 0
        var failedWriteCount = 0
        val streamedReply = StringBuilder()
        suspend fun emitReplyDelta(delta: String) {
            if (delta.isEmpty()) return
            streamedReply.append(delta)
            onProgress(CreationAgentProgressEvent(
                type = "reply_delta",
                message = "",
                status = "running",
                data = buildJsonObject { put("delta", delta) },
            ))
        }
        while (iteration < contract.maxIterations && finalReply.isBlank()) {
            onProgress(CreationAgentProgressEvent(
                type = "model_step_started",
                message = if (iteration == 0) "正在判断需要哪些立项能力…" else "正在根据真实工具结果继续处理…",
                data = buildJsonObject { put("iteration", iteration + 1) },
            ))
            val writesClosed = successfulWriteCount >= contract.maxSuccessfulWritesPerTurn ||
                failedWriteCount >= contract.maxFailedWritesPerTurn
            val scopedTools = if (categorySelected && writesClosed) {
                JsonArray(emptyList())
            } else {
                contract.toolSchemas(activeCategories)
            }
            val turn = directApi.streamAgentTurn(
                config = config,
                messages = messages,
                tools = scopedTools,
                toolChoice = when {
                    !categorySelected -> "required"
                    writesClosed -> null
                    else -> "auto"
                },
                maxOutputTokens = 6_000,
                temperature = 0.25,
                onContentDelta = ::emitReplyDelta,
            )
            promptMetrics += promptMetric(
                iteration = iteration + 1,
                phase = "standalone",
                activeCategories = activeCategories,
                messages = messages,
                tools = scopedTools,
                promptTokens = turn.promptTokens,
            )
            val calls = turn.toolCalls.take(12)
            if (calls.isEmpty()) {
                check(categorySelected) {
                    "模型没有调用本步骤唯一开放的 set_tool_categories，本轮未接受文字回复"
                }
                finalReply = turn.content.trim()
                break
            }

            val categoryCall = calls.firstOrNull { it.name == contract.categoryController }
            if (categoryCall != null) {
                val assistantToolMessage = assistantToolMessage(turn.content, listOf(categoryCall))
                messages += assistantToolMessage
                turnProtocolMessages += assistantToolMessage
                val selected = runCatching {
                    contract.normalizeCategories(
                        (categoryCall.arguments["enabled_categories"] as? JsonArray)
                            .orEmpty()
                            .mapNotNull { (it as? JsonPrimitive)?.contentOrNull },
                    )
                }
                val categoryResult = selected.fold(
                    onSuccess = contract::categoryResult,
                    onFailure = { result(categoryCall.name, "error", it.message ?: "工具类别参数无效") },
                )
                toolResults += categoryResult
                val toolMessage = buildJsonObject {
                    put("role", "tool")
                    put("tool_call_id", categoryCall.id)
                    put("content", categoryResult.toString().take(120_000))
                }
                messages += toolMessage
                turnProtocolMessages += toolMessage
                selected.getOrNull()?.let { categories ->
                    activeCategories = categories
                    categorySelected = true
                    onProgress(CreationAgentProgressEvent(
                        type = "tool_categories_changed",
                        message = categoryResult.string("detail"),
                        status = "ok",
                        data = categoryResult["data"] as? JsonObject ?: JsonObject(emptyMap()),
                    ))
                }
                iteration += 1
                continue
            }

            val assistantToolMessage = assistantToolMessage(turn.content, calls)
            messages += assistantToolMessage
            turnProtocolMessages += assistantToolMessage

            val availableTools = contract.availableToolNames(activeCategories)
            for (call in calls) {
                var attemptedWrite = false
                val execution = try {
                    when {
                        call.name !in availableTools -> ToolExecution(
                            working,
                            result(call.name, "skipped", "该工具当前未向立项会话开放"),
                        )
                        call.name in contract.writeToolNames &&
                            successfulWriteCount >= contract.maxSuccessfulWritesPerTurn -> ToolExecution(
                            working,
                            result(
                                call.name,
                                "denied",
                                "本条用户消息已经成功写入一次；本轮不得继续确认、生成或修改其他资料。请结束回复并等待作者的下一条消息。",
                                buildJsonObject { put("reason", "successful_write_limit") },
                            ),
                        )
                        call.name in contract.writeToolNames &&
                            failedWriteCount >= contract.maxFailedWritesPerTurn -> ToolExecution(
                            working,
                            result(
                                call.name,
                                "denied",
                                "本轮写入失败已达上限；为避免自动重试循环，本轮写工具已经关闭。",
                                buildJsonObject { put("reason", "failed_write_limit") },
                            ),
                        )
                        else -> {
                            attemptedWrite = call.name in contract.writeToolNames
                            onProgress(CreationAgentProgressEvent(
                                type = "tool_started",
                                message = "正在${toolLabel(call.name)}…",
                                data = buildJsonObject { put("tool", call.name) },
                            ))
                            execute(working, call.name, call.arguments, config)
                        }
                    }
                } catch (error: CancellationException) {
                    throw error
                } catch (error: Exception) {
                    ToolExecution(
                        working,
                        result(call.name, "error", error.message ?: "工具执行失败"),
                    )
                }
                working = execution.session
                execution.createdProjectId?.let { createdProjectId = it }
                toolResults += execution.result
                if (execution.wrote) persistSession(working)
                if (attemptedWrite) {
                    if (execution.result.string("status") in setOf("ok", "running")) {
                        successfulWriteCount += 1
                    } else {
                        failedWriteCount += 1
                    }
                }
                onProgress(CreationAgentProgressEvent(
                    type = "tool_completed",
                    message = execution.result.string("detail").ifBlank { "${toolLabel(call.name)}完成" },
                    status = execution.result.string("status").ifBlank { "ok" },
                    data = buildJsonObject {
                        put("tool", call.name)
                        put("label", toolLabel(call.name))
                    },
                ))
                if (attemptedWrite && failedWriteCount == contract.maxFailedWritesPerTurn) {
                    onProgress(CreationAgentProgressEvent(
                        type = "tool_completed",
                        message = "写入连续失败已达上限，本轮已停止自动重试",
                        status = "denied",
                        data = buildJsonObject {
                            put("tool", call.name)
                            put("turn_boundary", "failed_write_limit")
                            put("failed_writes", failedWriteCount)
                        },
                    ))
                }
                val toolMessage = buildJsonObject {
                    put("role", "tool")
                    put("tool_call_id", call.id)
                    put("content", execution.result.toString().take(120_000))
                }
                messages += toolMessage
                turnProtocolMessages += toolMessage
            }
            iteration += 1
        }

        if (finalReply.isBlank() && toolResults.isNotEmpty()) {
            onProgress(CreationAgentProgressEvent(
                type = "model_step_started",
                message = "正在根据真实写入结果整理回复…",
            ))
            val summaryMessages = messages + chatMessage(
                "user",
                "请根据以上真实工具结果，用两到四句中文说明本轮实际写入/读取了什么，然后提出一个基于当前数据缺口的后续问题。不要声称失败的写入已保存。",
            )
            // This is a text-only summary, matching the PC DeepSeek adapter's
            // plain-text path. Tool-capable turns above keep thinking enabled.
            val summaryExtraBody = if (config.isDeepSeek()) buildJsonObject {
                put("thinking", buildJsonObject { put("type", "disabled") })
            } else null
            val summaryTurn = runCatching {
                directApi.streamAgentTurn(
                    config = config,
                    messages = summaryMessages,
                    tools = JsonArray(emptyList()),
                    maxOutputTokens = 1_200,
                    temperature = 0.2,
                    extraBody = summaryExtraBody,
                    onContentDelta = ::emitReplyDelta,
                )
            }.getOrNull()
            promptMetrics += promptMetric(
                iteration = promptMetrics.size + 1,
                phase = "summary",
                activeCategories = activeCategories,
                messages = summaryMessages,
                tools = JsonArray(emptyList()),
                promptTokens = summaryTurn?.promptTokens,
            )
            finalReply = summaryTurn?.content?.trim().orEmpty()
        }
        if (createdProjectId != null) {
            finalReply = "正式作品已创建并进入作品库。请点击下方按钮进入正式作品；进入后项目助手会自动展开，后续正文与项目资料都在那里继续。"
        }
        if (finalReply.isBlank()) {
            val writes = toolResults.mapNotNull { it as? JsonObject }
                .filter { it.string("tool") in contract.writeToolNames && it.string("status") in setOf("ok", "running") }
                .map { it.string("detail") }
                .filter(String::isNotBlank)
                .take(3)
            finalReply = if (writes.isNotEmpty()) {
                "本轮已完成：${writes.joinToString("；")}。接下来你最想补充哪一部分？"
            } else truthfulNoWrite(toolResults)
        }
        if (!streamedReply.toString().endsWith(finalReply)) {
            emitReplyDelta(finalReply)
        }
        val modelMessages = buildJsonArray {
            add(userMessage)
            turnProtocolMessages.forEach(::add)
            add(chatMessage("assistant", finalReply.take(80_000)))
        }
        return MobileCreationConversationResult(
            working,
            finalReply,
            JsonArray(toolResults),
            modelMessages,
            replayable = true,
            status = "completed",
            createdProjectId = createdProjectId,
            promptMetrics = JsonArray(promptMetrics),
        )
    }

    private fun promptMetric(
        iteration: Int,
        phase: String,
        activeCategories: List<String>,
        messages: List<JsonObject>,
        tools: JsonArray,
        promptTokens: Int?,
    ): JsonObject {
        val systemPrompt = messages.firstOrNull { it.string("role") == "system" }
            ?.string("content")
            .orEmpty()
        val requestProjection = buildJsonObject {
            put("messages", JsonArray(messages))
            put("tools", tools)
        }.toString()
        return buildJsonObject {
            put("iteration", iteration)
            put("phase", phase)
            put("enabled_categories", JsonArray(activeCategories.map(::JsonPrimitive)))
            put("tool_count", tools.size)
            put("tool_schema_estimated_tokens", estimateTokens(tools.toString()))
            put("system_prompt_estimated_tokens", estimateTokens(systemPrompt))
            put("request_estimated_tokens", estimateTokens(requestProjection))
            if (promptTokens == null) put("prompt_tokens", JsonNull)
            else put("prompt_tokens", promptTokens.coerceAtLeast(0))
            put("usage_reported", promptTokens != null)
        }
    }

    private fun estimateTokens(text: String): Int {
        if (text.isEmpty()) return 0
        val cjkCount = text.count { char ->
            char in '\u4E00'..'\u9FFF' || char in '\u3400'..'\u4DBF'
        }
        return cjkCount + maxOf(1, (text.length - cjkCount) / 4)
    }

    private fun truthfulNoWrite(toolResults: List<JsonElement>): String {
        val results = toolResults.mapNotNull { it as? JsonObject }
        val failures = results
            .filter { it.string("status") !in setOf("ok", "running") }
            .map { it.string("detail").ifBlank { "工具未完成" } }
        if (failures.isNotEmpty()) return "本轮没有保存任何修改：${failures.last()}。请调整要求后重试。"
        val readSucceeded = results.any {
            it.string("status") == "ok" && it.string("tool") !in contract.writeToolNames
        }
        if (readSucceeded) return "本轮只完成了立项工具读取，没有保存任何修改。请明确要写入的对象和内容后重试。"
        if (results.isNotEmpty()) return "本轮执行了立项工具，但没有产生可确认的写入。请调整要求后重试。"
        return "本轮未执行任何立项工具，因此没有读取或修改立项数据。请重试。"
    }

    private suspend fun execute(
        source: JsonObject,
        tool: String,
        args: JsonObject,
        config: DirectApiConfig,
    ): ToolExecution {
        if (tool !in contract.toolNames) {
            return ToolExecution(source, result(tool, "skipped", "该工具不属于当前立项 Agent 契约"))
        }
        val expected = args.intOrNull("expected_revision")
        if (tool in contract.revisionToolNames && expected != null && expected != source.int("revision")) {
            return ToolExecution(
                source,
                result(tool, "error", "Novel creation session revision conflict", buildJsonObject {
                    put("failure_class", "revision_conflict")
                    put("current_revision", source.int("revision"))
                }),
            )
        }
        return when (tool) {
            "get_creation_session", "get_creation_snapshot" -> ToolExecution(
                source,
                result(tool, "ok", "已读取当前立项快照", snapshot(source)),
            )
            "get_creation_artifact" -> {
                val artifact = args.string("artifact")
                ToolExecution(source, result(tool, "ok", "已读取${stageLabel(artifact)}", artifactSnapshot(source, artifact)))
            }
            "list_creation_artifacts" -> ToolExecution(
                source,
                result(tool, "ok", "已读取全部立项对象", buildJsonObject {
                    put("revision", source.int("revision"))
                    put("artifacts", artifactSummaries(source))
                }),
            )
            "get_creation_dependencies", "get_creation_dependency_graph" -> ToolExecution(
                source,
                result(tool, "ok", "已读取立项依赖关系", dependencySnapshot(args.string("artifact"))),
            )
            "validate_creation_consistency", "validate_creation_session" -> ToolExecution(
                source,
                result(tool, "ok", "已检查当前立项完整性", localValidation(source)),
            )
            "patch_creation_session" -> patchSession(source, args)
            "patch_creation_artifact" -> patchArtifact(source, args)
            "lock_creation_fields" -> setLocks(source, args, true)
            "unlock_creation_fields" -> setLocks(source, args, false)
            "list_creation_entities" -> ToolExecution(
                source,
                result(tool, "ok", "已读取立项实体", buildJsonObject {
                    put("revision", source.int("revision"))
                    put("entities", JsonArray(listEntities(source, args.string("artifact"), args.string("entity_type"))))
                }),
            )
            "get_creation_entity" -> {
                val entity = resolveEntity(source, args.string("entity_id"))
                if (entity == null) ToolExecution(source, result(tool, "skipped", "未找到目标立项实体"))
                else ToolExecution(source, result(tool, "ok", "已读取目标立项实体", entity.descriptor))
            }
            "patch_creation_entity" -> patchEntity(source, args)
            "delete_creation_entity" -> deleteEntity(source, args)
            "confirm_creation_artifact" -> {
                val artifact = args.string("artifact")
                if ("data" in args) {
                    ToolExecution(
                        source,
                        result(tool, "error", "确认工具不能同时修改内容；请先保存修改，再由作者确认当前版本"),
                    )
                } else {
                    val updated = stageAgent.confirmStage(source, artifact)
                    ToolExecution(updated, result(tool, "ok", "${stageLabel(artifact)}已确认", artifactSnapshot(updated, artifact)), wrote = true)
                }
            }
            "generate_creation_artifact", "refine_creation_artifact", "regenerate_creation_artifact" ->
                generateArtifact(source, tool, args, config)
            "finalize_creation_session" -> {
                val validation = localValidation(source)
                if ((validation["ready"] as? JsonPrimitive)?.contentOrNull?.toBooleanStrictOrNull() != true) {
                    ToolExecution(source, result(tool, "error", "当前立项数据还没有达到正式建档条件", validation))
                } else {
                    val (updated, projectId) = finalizeSession(source)
                    ToolExecution(
                        updated,
                        result(tool, "ok", "正式作品已创建", buildJsonObject { put("project_id", projectId) }),
                        wrote = true,
                        createdProjectId = projectId,
                    )
                }
            }
            "get_creation_operation", "cancel_creation_operation", "pause_creation_operation",
            "resume_creation_operation", "retry_creation_operation" -> ToolExecution(
                source,
                result(tool, "skipped", "手机独立模式的单轮立项工具同步完成，不存在独立后台 Operation"),
            )
            "undo_creation_artifact", "list_creation_artifact_versions", "get_creation_artifact_diff",
            "restore_creation_artifact_version" -> ToolExecution(
                source,
                result(tool, "skipped", "手机独立草稿当前不提供跨版本工具；现有内容未修改"),
            )
            "preview_creation_import", "apply_creation_import" -> ToolExecution(
                source,
                result(tool, "skipped", "手机独立对话式立项暂不在 Agent 内执行文件导入"),
            )
            else -> ToolExecution(source, result(tool, "skipped", "手机独立模式暂未实现该立项工具"))
        }
    }

    private fun patchSession(source: JsonObject, args: JsonObject): ToolExecution {
        val changes = args["changes"] as? JsonObject ?: JsonObject(emptyMap())
        if (changes.isEmpty()) return ToolExecution(source, result("patch_creation_session", "skipped", "没有可写入的会话变化"))
        val draft = source.objectValue("draft").toMutableMap()
        val form = (draft["form"] as? JsonObject ?: JsonObject(emptyMap())).toMutableMap()
        changes.forEach { (key, value) ->
            when (key) {
                "creation_mode", "author_brief", "author_outline", "locked_requirements", "selected_concept_id", "quick_mode" -> draft[key] = value
                "form" -> (value as? JsonObject)?.forEach { (formKey, formValue) -> form[formKey] = formValue }
                "display_title" -> Unit
                else -> form[key] = value
            }
        }
        draft["form"] = JsonObject(form)
        val stages = (draft["stages"] as? JsonObject ?: JsonObject(emptyMap())).toMutableMap()
        val constraints = (stages["constraints"] as? JsonObject ?: JsonObject(emptyMap())).toMutableMap()
        constraints["status"] = JsonPrimitive("generated")
        constraints["data"] = JsonObject(form)
        constraints["source"] = JsonPrimitive("assistant")
        constraints["updated_at"] = JsonPrimitive(Instant.now().toString())
        stages["constraints"] = JsonObject(constraints)
        draft["stages"] = JsonObject(stages)
        val updated = bump(source, draft) { root ->
            changes["display_title"]?.let { root["display_title"] = it }
        }
        return ToolExecution(
            updated,
            result("patch_creation_session", "ok", "立项会话已增量更新", snapshot(updated)),
            wrote = true,
        )
    }

    private fun patchArtifact(source: JsonObject, args: JsonObject): ToolExecution {
        val artifact = args.string("artifact")
        val current = source.stageData(artifact)
        if (current.isEmpty()) {
            return ToolExecution(source, result("patch_creation_artifact", "error", "${stageLabel(artifact)}尚无可局部修改的数据；请先生成该对象"))
        }
        val changes = (args["changes"] as? JsonArray).orEmpty().mapNotNull { it as? JsonObject }
        if (changes.isEmpty()) return ToolExecution(source, result("patch_creation_artifact", "skipped", "没有可应用的局部修改"))
        val patched = try {
            applyChanges(current, changes)
        } catch (error: Exception) {
            return ToolExecution(source, result("patch_creation_artifact", "error", error.message ?: "局部修改无效"))
        }
        val updated = try {
            stageAgent.replaceArtifact(source, artifact, patched, "assistant")
        } catch (error: Exception) {
            return ToolExecution(source, result("patch_creation_artifact", "error", error.message ?: "修改后数据未通过校验"))
        }
        return ToolExecution(updated, result("patch_creation_artifact", "ok", "${stageLabel(artifact)}已局部更新", artifactSnapshot(updated, artifact)), wrote = true)
    }

    private fun patchEntity(source: JsonObject, args: JsonObject): ToolExecution {
        val entity = resolveEntity(source, args.string("entity_id"))
            ?: return ToolExecution(source, result("patch_creation_entity", "skipped", "未找到目标立项实体"))
        val changes = (args["changes"] as? JsonArray).orEmpty().mapNotNull { it as? JsonObject }
        val patched = try { applyChanges(entity.data, changes) } catch (error: Exception) {
            return ToolExecution(source, result("patch_creation_entity", "error", error.message ?: "实体修改无效"))
        }
        val artifactData = source.stageData(entity.artifact).toMutableMap()
        val rows = (artifactData[entity.field] as? JsonArray).orEmpty().toMutableList()
        rows[entity.index] = patched
        artifactData[entity.field] = JsonArray(rows)
        val updated = try { stageAgent.replaceArtifact(source, entity.artifact, JsonObject(artifactData), "assistant") } catch (error: Exception) {
            return ToolExecution(source, result("patch_creation_entity", "error", error.message ?: "实体修改后未通过校验"))
        }
        val next = resolveEntity(updated, entity.id)?.descriptor ?: JsonNull
        return ToolExecution(updated, result("patch_creation_entity", "ok", "立项实体已更新", next), wrote = true)
    }

    private fun deleteEntity(source: JsonObject, args: JsonObject): ToolExecution {
        val entity = resolveEntity(source, args.string("entity_id"))
            ?: return ToolExecution(source, result("delete_creation_entity", "skipped", "未找到目标立项实体"))
        val artifactData = source.stageData(entity.artifact).toMutableMap()
        val rows = (artifactData[entity.field] as? JsonArray).orEmpty().toMutableList()
        rows.removeAt(entity.index)
        artifactData[entity.field] = JsonArray(rows)
        val updated = try { stageAgent.replaceArtifact(source, entity.artifact, JsonObject(artifactData), "assistant") } catch (error: Exception) {
            return ToolExecution(source, result("delete_creation_entity", "error", error.message ?: "删除后数据未通过校验"))
        }
        return ToolExecution(updated, result("delete_creation_entity", "ok", "立项实体已删除"), wrote = true)
    }

    private suspend fun generateArtifact(
        source: JsonObject,
        tool: String,
        args: JsonObject,
        config: DirectApiConfig,
    ): ToolExecution {
        val artifact = args.string("artifact")
        if (artifact == "all") {
            return ToolExecution(source, result(tool, "error", "对话式立项请按实际缺口逐个生成对象，不使用一次性 all 阶段"))
        }
        if (artifact !in contract.stageOrder || artifact == "constraints") {
            return ToolExecution(source, result(tool, "error", "未知或不可生成的立项对象：$artifact"))
        }
        val instruction = args.string("instruction")
        val entityId = args.string("entity_id")
        val entityType = args.string("entity_type")
        val generated = try {
            stageAgent.generateStage(source, artifact, instruction, config)
        } catch (error: Exception) {
            return ToolExecution(source, result(tool, "error", error.message ?: "${stageLabel(artifact)}生成失败"))
        }
        var updated = generated
        if (entityId.isNotBlank()) {
            val target = resolveEntity(source, entityId)
            if (target != null) {
                val oldArtifact = source.stageData(target.artifact).toMutableMap()
                val oldRows = (oldArtifact[target.field] as? JsonArray).orEmpty().toMutableList()
                val generatedRows = (generated.stageData(target.artifact)[target.field] as? JsonArray).orEmpty()
                val replacement = generatedRows.getOrNull(target.index) as? JsonObject
                if (replacement != null && target.index in oldRows.indices) {
                    oldRows[target.index] = replacement
                    oldArtifact[target.field] = JsonArray(oldRows)
                    updated = stageAgent.replaceArtifact(source, target.artifact, JsonObject(oldArtifact), "model")
                }
            }
        } else if (entityType.isNotBlank()) {
            updated = mergeOnlyNewEntities(source, generated, artifact, entityType)
        }
        return ToolExecution(
            updated,
            result(tool, "ok", "${stageLabel(artifact)}已生成并写入草稿", artifactSnapshot(updated, artifact)),
            wrote = true,
        )
    }

    private fun mergeOnlyNewEntities(
        original: JsonObject,
        generated: JsonObject,
        artifact: String,
        entityType: String,
    ): JsonObject {
        val mapping = entityFieldMapping(artifact, entityType) ?: return generated
        val (field, _) = mapping
        val oldData = original.stageData(artifact)
        if (oldData.isEmpty()) return generated
        val newData = generated.stageData(artifact)
        val oldRows = (oldData[field] as? JsonArray).orEmpty().mapNotNull { it as? JsonObject }
        val newRows = (newData[field] as? JsonArray).orEmpty().mapNotNull { it as? JsonObject }
        val oldKeys = oldRows.map(::entityKey).filter(String::isNotBlank).toSet()
        val additions = newRows.filter { entityKey(it).let { key -> key.isBlank() || key !in oldKeys } }
        if (additions.isEmpty()) return original
        val mergedData = oldData.toMutableMap()
        mergedData[field] = JsonArray(oldRows + additions)
        return stageAgent.replaceArtifact(original, artifact, JsonObject(mergedData), "model")
    }

    private fun setLocks(source: JsonObject, args: JsonObject, locked: Boolean): ToolExecution {
        val artifact = args.string("artifact")
        val paths = (args["paths"] as? JsonArray).orEmpty().mapNotNull { (it as? JsonPrimitive)?.contentOrNull }
        val draft = source.objectValue("draft").toMutableMap()
        val locks = (draft["artifact_locks"] as? JsonObject ?: JsonObject(emptyMap())).toMutableMap()
        val current = (locks[artifact] as? JsonArray).orEmpty().mapNotNull { (it as? JsonPrimitive)?.contentOrNull }.toMutableSet()
        if (locked) current.addAll(paths) else current.removeAll(paths.toSet())
        locks[artifact] = JsonArray(current.sorted().map(::JsonPrimitive))
        draft["artifact_locks"] = JsonObject(locks)
        val updated = bump(source, draft)
        return ToolExecution(updated, result(if (locked) "lock_creation_fields" else "unlock_creation_fields", "ok", "字段锁定状态已更新"), wrote = true)
    }

    private fun snapshot(source: JsonObject): JsonObject = buildJsonObject {
        put("id", source.string("id"))
        put("revision", source.int("revision"))
        put("status", source.string("status"))
        put("user_brief", source.string("user_brief"))
        put("display_title", source.string("display_title"))
        put("draft", CreationAgentTurnRecords.agentVisibleDraft(source))
        put("artifacts", artifactSummaries(source))
    }

    private fun artifactSummaries(source: JsonObject): JsonArray = buildJsonArray {
        contract.stageOrder.forEach { artifact ->
            val state = source.stageState(artifact)
            add(buildJsonObject {
                put("artifact", artifact)
                put("label", stageLabel(artifact))
                put("status", state.string("status").ifBlank { "pending" })
                put("source", state.string("source"))
                put("data", state["data"] ?: JsonNull)
            })
        }
    }

    private fun artifactSnapshot(source: JsonObject, artifact: String): JsonObject = buildJsonObject {
        val state = source.stageState(artifact)
        put("artifact", artifact)
        put("label", stageLabel(artifact))
        put("revision", source.int("revision"))
        put("status", state.string("status").ifBlank { "pending" })
        put("source", state.string("source"))
        put("data", state["data"] ?: JsonNull)
    }

    private fun dependencySnapshot(artifact: String): JsonObject = buildJsonObject {
        put("artifact", artifact)
        put("downstream", JsonArray(contract.impactDependencies[artifact].orEmpty().map(::JsonPrimitive)))
        put("graph", buildJsonObject {
            contract.impactDependencies.forEach { (key, value) ->
                put(key, JsonArray(value.map(::JsonPrimitive)))
            }
        })
    }

    private fun localValidation(source: JsonObject): JsonObject {
        val required = listOf("constraints", "concepts", "world_style", "characters", "locations", "macro_outline")
        val missing = required.filter { source.stageState(it).string("status") != "confirmed" }
        val review = source.stageData("final_review")
        val reviewReady = source.stageState("final_review").string("status") in setOf("generated", "confirmed") &&
            (review["ready"] as? JsonPrimitive)?.contentOrNull?.toBooleanStrictOrNull() == true
        return buildJsonObject {
            put("ready", missing.isEmpty() && reviewReady)
            put("revision", source.int("revision"))
            put("missing_confirmations", JsonArray(missing.map(::JsonPrimitive)))
            put("final_review_ready", reviewReady)
        }
    }

    private fun listEntities(source: JsonObject, artifactFilter: String, typeFilter: String): List<JsonObject> {
        val result = mutableListOf<JsonObject>()
        ENTITY_FIELDS.forEach { (artifact, mappings) ->
            if (artifactFilter.isNotBlank() && artifact != artifactFilter) return@forEach
            val data = source.stageData(artifact)
            mappings.forEach mapping@{ (field, type) ->
                if (typeFilter.isNotBlank() && type != typeFilter) return@mapping
                (data[field] as? JsonArray).orEmpty().mapNotNull { it as? JsonObject }.forEachIndexed { index, row ->
                    result += entityDescriptor(artifact, field, type, index, row)
                }
            }
        }
        return result
    }

    private fun resolveEntity(source: JsonObject, entityId: String): LocalCreationEntity? {
        val parts = entityId.split(':')
        if (parts.size != 3) return null
        val artifact = parts[0]
        val field = parts[1]
        val index = parts[2].toIntOrNull() ?: return null
        val type = ENTITY_FIELDS[artifact]?.firstOrNull { it.first == field }?.second ?: return null
        val data = ((source.stageData(artifact)[field] as? JsonArray)?.getOrNull(index) as? JsonObject) ?: return null
        return LocalCreationEntity(entityId, artifact, field, type, index, data, entityDescriptor(artifact, field, type, index, data))
    }

    private fun entityDescriptor(artifact: String, field: String, type: String, index: Int, data: JsonObject): JsonObject = buildJsonObject {
        put("id", "$artifact:$field:$index")
        put("artifact", artifact)
        put("entity_type", type)
        put("entity_key", entityKey(data).ifBlank { "$field-$index" })
        put("data", data)
    }

    private fun entityKey(data: JsonObject): String =
        data.string("name").ifBlank { data.string("title") }.ifBlank { data.string("id") }

    private fun entityFieldMapping(artifact: String, entityType: String): Pair<String, String>? =
        ENTITY_FIELDS[artifact]?.firstOrNull { it.second == entityType }

    private fun applyChanges(source: JsonObject, changes: List<JsonObject>): JsonObject {
        var current: JsonElement = source
        changes.forEach { change ->
            val action = change.string("action").ifBlank {
                when (change.string("op")) {
                    "add" -> if (change.string("path").endsWith("/-")) "append" else "set"
                    "replace" -> "replace"
                    "remove" -> "remove"
                    else -> "set"
                }
            }
            var path = change.string("path")
            if (path.endsWith("/-")) path = path.removeSuffix("/-")
            val parts = path.trim('/').takeIf(String::isNotBlank)?.split('/')
                ?.map { it.replace("~1", "/").replace("~0", "~") }
                ?: emptyList()
            current = mutate(current, parts, action, change["value"], change.intOrNull("target_count"), change["fill_value"])
        }
        return current as? JsonObject ?: error("立项对象根节点必须保持为 JSON 对象")
    }

    private fun mutate(
        current: JsonElement,
        parts: List<String>,
        action: String,
        value: JsonElement?,
        targetCount: Int?,
        fillValue: JsonElement?,
    ): JsonElement {
        if (parts.isEmpty()) {
            return when (action) {
                "append" -> JsonArray((current as? JsonArray).orEmpty() + (value ?: JsonNull))
                "resize" -> {
                    val rows = (current as? JsonArray).orEmpty().toMutableList()
                    val target = targetCount ?: rows.size
                    while (rows.size > target) rows.removeAt(rows.lastIndex)
                    while (rows.size < target) rows += fillValue ?: JsonNull
                    JsonArray(rows)
                }
                "remove" -> JsonNull
                else -> value ?: current
            }
        }
        return when (current) {
            is JsonObject -> {
                val key = parts.first()
                val map = current.toMutableMap()
                if (parts.size == 1 && action == "remove") {
                    map.remove(key)
                } else {
                    val child = map[key] ?: if (parts.size == 1) JsonNull else JsonObject(emptyMap())
                    map[key] = mutate(child, parts.drop(1), action, value, targetCount, fillValue)
                }
                JsonObject(map)
            }
            is JsonArray -> {
                val index = parts.first().toIntOrNull() ?: error("数组路径必须使用数字下标")
                val rows = current.toMutableList()
                require(index in rows.indices) { "数组路径超出范围" }
                if (parts.size == 1 && action == "remove") rows.removeAt(index)
                else rows[index] = mutate(rows[index], parts.drop(1), action, value, targetCount, fillValue)
                JsonArray(rows)
            }
            else -> error("JSON Pointer 指向了不可继续展开的值")
        }
    }

    private fun bump(
        source: JsonObject,
        draftMap: MutableMap<String, JsonElement>,
        rootChange: (MutableMap<String, JsonElement>) -> Unit = {},
    ): JsonObject {
        val now = Instant.now().toString()
        draftMap["updated_at"] = JsonPrimitive(now)
        val root = source.toMutableMap()
        root["draft"] = JsonObject(draftMap)
        root["revision"] = JsonPrimitive(source.int("revision") + 1)
        root["updated_at"] = JsonPrimitive(now)
        rootChange(root)
        return JsonObject(root)
    }

    private fun result(tool: String, status: String, detail: String, data: JsonElement? = null): JsonObject = buildJsonObject {
        put("tool", tool)
        put("status", status)
        put("detail", detail)
        if (data != null) put("data", data)
    }

    private fun chatMessage(role: String, content: String): JsonObject = buildJsonObject {
        put("role", role)
        put("content", content)
    }

    private fun assistantToolMessage(
        content: String,
        calls: List<DirectAgentToolCall>,
    ): JsonObject = buildJsonObject {
        put("role", "assistant")
        put("content", content)
        put("tool_calls", buildJsonArray {
            calls.forEach { call ->
                add(buildJsonObject {
                    put("id", call.id)
                    put("type", "function")
                    put("function", buildJsonObject {
                        put("name", call.name)
                        put("arguments", call.arguments.toString())
                    })
                })
            }
        })
    }

    private fun stageLabel(stage: String): String = contract.stageLabels[stage] ?: stage

    private fun toolLabel(tool: String): String = when (tool) {
        "get_creation_snapshot", "get_creation_session" -> "读取当前立项"
        "patch_creation_session" -> "写入创作约束"
        "patch_creation_artifact" -> "增量写入结构化资料"
        "generate_creation_artifact" -> "生成缺失的立项对象"
        "refine_creation_artifact" -> "定向调整立项对象"
        "confirm_creation_artifact" -> "确认立项对象"
        "finalize_creation_session" -> "创建正式作品"
        else -> tool
    }

    private fun JsonObject.objectValue(name: String): JsonObject = get(name) as? JsonObject ?: JsonObject(emptyMap())
    private fun JsonObject.string(name: String): String = (get(name) as? JsonPrimitive)?.contentOrNull.orEmpty()
    private fun JsonObject.int(name: String): Int = (get(name) as? JsonPrimitive)?.intOrNull ?: 0
    private fun JsonObject.intOrNull(name: String): Int? = (get(name) as? JsonPrimitive)?.intOrNull
    private fun JsonObject.stageState(stage: String): JsonObject = objectValue("draft").objectValue("stages").objectValue(stage)
    private fun JsonObject.stageData(stage: String): JsonObject = stageState(stage)["data"] as? JsonObject ?: JsonObject(emptyMap())
    private fun DirectApiConfig.isDeepSeek(): Boolean =
        listOf(displayName, baseUrl, model).any { it.contains("deepseek", ignoreCase = true) }

    private data class ToolExecution(
        val session: JsonObject,
        val result: JsonObject,
        val wrote: Boolean = false,
        val createdProjectId: String? = null,
    )

    private data class LocalCreationEntity(
        val id: String,
        val artifact: String,
        val field: String,
        val type: String,
        val index: Int,
        val data: JsonObject,
        val descriptor: JsonObject,
    )

    private companion object {
        val ENTITY_FIELDS = mapOf(
            "world_style" to listOf("worldbuilding" to "worldbuilding"),
            "characters" to listOf("characters" to "character", "relationships" to "relationship"),
            "locations" to listOf("entries" to "location", "relations" to "world_relation"),
            "macro_outline" to listOf("volumes" to "volume"),
            "opening_outline" to listOf("chapters" to "chapter_outline", "sections" to "scene_outline"),
        )
    }
}
