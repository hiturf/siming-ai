package com.siming.mobile.data.creation

import com.siming.mobile.data.network.DirectApiClient
import java.io.File
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.boolean
import kotlinx.serialization.json.buildJsonArray
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.int
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.put
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class MobileCreationAgentTest {
    private val rawContract = contractJson()
    private val agent = MobileCreationAgent(rawContract, DirectApiClient())

    @Test
    fun everyDeterministicBaselineMatchesTheExportedPcFixture() {
        val creation = (Json.parseToJsonElement(rawContract) as JsonObject).objectValue("creation")
        val fixture = creation.objectValue("deterministic_baseline_fixture")
        val session = fixture.objectValue("session")
        val expected = fixture.objectValue("expected")

        expected.forEach { (stage, data) ->
            assertEquals("PC baseline drifted at $stage", data, agent.baseline(session, stage))
        }
    }

    @Test
    fun everyStageNormalizerMatchesTheExportedPcFixture() {
        val creation = (Json.parseToJsonElement(rawContract) as JsonObject).objectValue("creation")
        val fixture = creation.objectValue("normalization_fixture")
        val baselines = fixture.objectValue("baseline")
        val raw = fixture.objectValue("raw")
        val expected = fixture.objectValue("expected")

        expected.forEach { (stage, data) ->
            assertEquals(
                "PC normalizer drifted at $stage",
                data,
                agent.normalizeStage(stage, raw.objectValue(stage), baselines.objectValue(stage)),
            )
        }
    }

    @Test
    fun deterministicStagesMatchPcV3CompactBaseline() {
        var session = agent.start(
            CreationStartInput(
                creationMode = "author_led",
                brief = "林舟在会吞噬记忆的城里寻找失踪姐姐。",
                presetId = "xuanhuan",
                authorOutline = "全书必须分为四卷。",
                lockedRequirements = listOf("主角必须叫林舟"),
            ),
        )
        session = agent.confirmStage(session, "constraints")
        session = agent.confirmStage(session, "concepts", buildJsonObject {
            put("options", buildJsonArray {
                add(buildJsonObject {
                    put("id", "concept-1")
                    put("source_index", 0)
                    put("title", "记忆城")
                    put("subtitle", "记忆即货币")
                    put("logline", "林舟必须在记忆耗尽前找到姐姐。")
                    put("protagonist_seed", buildJsonObject {
                        put("name", "林舟")
                        put("identity", "记忆修复师")
                        put("goal", "找到姐姐")
                        put("lack", "不敢信任他人")
                    })
                    put("world_hook", "城中的每次交易都会消耗一段记忆。")
                    put("core_conflict", "救回姐姐与保住自我记忆不可兼得。")
                    put("story_engine", "")
                    put("opening_hook", "林舟醒来时忘了姐姐的脸。")
                    put("differentiators", buildJsonArray {})
                    put("risks", buildJsonArray {})
                    put("coverage", buildJsonObject {
                        put("score", 100)
                        put("covered", buildJsonArray {})
                        put("missing", buildJsonArray {})
                    })
                })
            })
        })

        val world = agent.baseline(session, "world_style")
        assertEquals(6, world.array("forbidden_patterns").size)
        assertEquals(
            world.array("forbidden_patterns").take(3),
            world.array("forbidden_patterns").drop(3),
        )
        assertEquals(
            listOf("世界规则", "力量与资源", "社会与文化", "历史与冲突", "生活与感官"),
            world.array("display_groups").map { (it as JsonPrimitive).content },
        )

        val characters = agent.baseline(session, "characters")
        val protagonist = characters.array("characters").first().jsonObject
        assertEquals("protagonist", protagonist.string("role_type"))
        assertEquals("相信行动能够改变自身处境", protagonist.objectValue("profile").string("core_belief"))
        assertEquals("不主动牺牲无辜者", protagonist.objectValue("profile").string("moral_taboo"))

        val locations = agent.baseline(session, "locations")
        assertEquals("核心世界钩子", locations.array("entries").first().jsonObject.string("title"))

        val macro = agent.baseline(session, "macro_outline")
        assertEquals(1, macro.array("volumes").size)
        assertEquals(240, macro.array("volumes").single().jsonObject["end_chapter"]!!.jsonPrimitive.int)

        val opening = agent.baseline(session, "opening_outline")
        assertEquals("第1章 林舟醒来时忘了姐姐的脸。", opening.array("chapters").first().jsonObject.string("title"))
        assertEquals(3, opening.array("chapters").size)
        assertEquals(9, opening.array("sections").size)
        assertEquals("每章3个场景事件，允许作者调整为2至6个", opening.string("section_rule"))

        session = agent.confirmStage(session, "world_style", world)
        session = agent.confirmStage(session, "characters", characters)
        session = agent.confirmStage(session, "locations", locations)
        session = agent.confirmStage(session, "macro_outline", macro)
        val reviewWithoutOpening = agent.baseline(session, "final_review")
        assertTrue(reviewWithoutOpening["ready"]!!.jsonPrimitive.boolean)
        assertTrue(
            reviewWithoutOpening.array("warnings").any {
                (it as JsonPrimitive).content.contains("开篇细纲尚未确认")
            },
        )
        session = agent.confirmStage(session, "opening_outline", opening)
        val review = agent.baseline(session, "final_review")
        assertTrue(review["ready"]!!.jsonPrimitive.boolean)
        assertEquals(1, review.objectValue("counts")["characters"]!!.jsonPrimitive.int)
        assertEquals(9, review.objectValue("counts")["sections"]!!.jsonPrimitive.int)
    }

    @Test
    fun conceptConfirmationUpdatesPcTopLevelDraftFieldsOnce() {
        val started = agent.start(CreationStartInput(creationMode = "explore", brief = "一个关于选择的故事"))
        val revision = started["revision"]!!.jsonPrimitive.int
        val confirmed = agent.confirmStage(started, "concepts", conceptData())
        val draft = confirmed.objectValue("draft")

        assertEquals(revision + 1, confirmed["revision"]!!.jsonPrimitive.int)
        assertEquals("concept-1", draft.string("selected_concept_id"))
        assertEquals(2, draft.array("concepts").size)
        assertTrue(draft.objectValue("concept_seeds").containsKey("concept-1"))
    }

    private fun conceptData(): JsonObject = buildJsonObject {
        put("options", buildJsonArray {
            add(buildJsonObject {
                put("id", "concept-1")
                put("title", "选择")
                put("logline", "一个人必须在两种人生之间做出选择。")
                put("protagonist_seed", buildJsonObject {
                    put("name", "周一")
                    put("identity", "普通人")
                    put("goal", "作出选择")
                    put("lack", "害怕承担后果")
                })
                put("world_hook", "选择会生成可见的分岔世界。")
                put("core_conflict", "两种人生无法同时保留。")
                put("opening_hook", "两个自己同时敲响家门。")
            })
            add(buildJsonObject {
                put("id", "concept-2")
                put("title", "不选")
                put("logline", "一个人拒绝两种人生后，必须创造第三条路。")
                put("protagonist_seed", buildJsonObject {
                    put("name", "周一")
                    put("identity", "普通人")
                    put("goal", "创造新选择")
                    put("lack", "不相信自己能改变规则")
                })
                put("world_hook", "被拒绝的选择会反过来追逐选择者。")
                put("core_conflict", "拒绝选择会同时失去两种人生。")
                put("opening_hook", "家门外出现第三个从未存在的自己。")
            })
        })
    }

    private fun contractJson(): String {
        val candidates = listOf(
            File("src/main/assets/pc_workspace_prompt_contract.json"),
            File("app/src/main/assets/pc_workspace_prompt_contract.json"),
        )
        return candidates.first(File::isFile).readText(Charsets.UTF_8)
    }

    private fun JsonObject.array(name: String): JsonArray = get(name) as JsonArray
    private fun JsonObject.objectValue(name: String): JsonObject = get(name) as? JsonObject ?: JsonObject(emptyMap())
    private fun JsonObject.string(name: String): String = (get(name) as? JsonPrimitive)?.contentOrNull.orEmpty()
}
