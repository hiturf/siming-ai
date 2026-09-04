---
id: continuity.cataloging.candidates
version: 3.1.20
scope: continuity
visibility: both
inputs: []
output_format: jsonl
tool_policy: none
tools:
  - inspect_story_granularity
  - repair_story_granularity
  - get_narrative_ledger
budget:
  fixed_chars: 8000
  context_chars: 80000
golden_cases:
  - name: required-granularity
    required_text: ["chapter_summary", "chapter_outline", "首个响应对象", "coverage_manifest", "relationships", "character_profiles", "character_state_update", "node_type=\"section\"", "chapter_link", "JSONL"]
  - name: incremental-repair
    required_text: ["增量修复回合", "保留上一轮", "缺失身份", "既有设定", "身份未确认"]
  - name: narrative-ledger
    required_text: ["narrative_state", "narrative_review", "resolves_item_id", "不得按标题猜测关闭"]
  - name: anonymous-role-boundary
    required_text: ["未具名岗位", "不得创建角色卡", "合并为同一个既有角色"]
  - name: state-field-ownership
    required_text: ["通话另一端", "省略 current_location", "appearance_before", "appearance_evidence", "age_before", "age_evidence", "items_or_assets 是整字段替换", "items_or_assets_before", "逐字包含", "同场另一人物"]
  - name: relationship-and-link-identity
    required_text: ["同一有向角色对", "一个当前 relationship_type", "每个角色只出现一次", "一个 appearance_type"]
  - name: worldbuilding-anti-fragmentation
    required_text: ["独立生命周期", "操作视角", "阶段汇总", "仅声称“层级不同”不是有效的新建理由"]
  - name: stable-background-preservation
    required_text: ["background_before", "逐字包含该完整旧值", "禁止改写、缩短或删除旧背景"]
---
依本章事实和已有档案生成可写库的候选 JSONL。

【输出】
- 只输出 JSONL；每行一个完整 JSON 对象，不要 Markdown、解释、代码块或数组。
- 首次生成回合（用户消息不含“上一轮校验未通过”）的首个响应对象必须一次性交付两个必填对象：
  `{{"chapter_summary":{{"summary_text":"...","coverage_manifest":{{"scene_count":1,"characters":[],"worldbuilding":[],"relationships":[],"character_profiles":[]}},"narrative_state":{{"events":[],"timeline_events":[],"foreshadowing_planted":[],"foreshadowing_resolved":[],"storyline_progress":[],"new_storylines":[],"reader_known_facts":[],"character_known_facts":[],"unresolved_actions":[]}},"narrative_review":{{"source":"provided","outcome":"assessed"}}}},"chapter_outline":{{"title":"当前章节原题","summary":"...","node_type":"chapter","status":"completed"}}}}`
  系统会把它拆成 chapter_summary 与章级 outline_create。首次生成时不能只返回摘要后结束，也不能先输出摘要、把大纲留到后续响应。增量修复回合不重复该骨架，只补校验明确指出的缺失或失败候选。
- 除首次生成回合的首个必填骨架对象外，禁止把 character_state_updates、worldbuilding_entries、outline_creates、chapter_links 等打包进总对象；其他每张候选必须独占一行并带标准 type。
- 没有角色、新设定、关系或角色档案变化时，coverage_manifest 对应项必须显式写 []；空数组是合法结果，禁止为满足格式虚构候选。
- chapter_summary 必须包含非空 summary_text，用一段话概括本章已经发生的主要事件；同时显式包含完整 narrative_state，没有发现时各数组也写 []，并提供 narrative_review，不能用字段缺失表示“没有问题”。
- chapter_summary 必须包含 `coverage_manifest`：`scene_count` 是本章独立场景数，`characters` 是本章全部出场或被提及且影响连续性的稳定、可持续识别角色名，`worldbuilding` 是本章新增、变化或被关键引用的设定标题，`relationships` 是本章明确确认、首次出现或改变且影响连续性的角色关系对象（source_name、target_name、relationship_type），`character_profiles` 是本章新建角色或稳定档案有新增信息的角色名；即使没有也必须写 1、[]、[]、[]、[]，不得省略。该清单是验收合同，不是备注。
- 多场景章节额外输出 2-6 条 node_type="section" 的 outline_create，以 parent_title 绑定章节点。
- 模型不得生成或猜测数据库 UUID。更新已有角色必须填写从真实档案读取的 id；新建角色不填写 id。新建 section 只填写 parent_title，真实父级 ID 由司命解析。
- 本候选回合不输出 chapter_overview、character_fact 等事实记录。

【角色与世界观】
- 同一批候选中的角色身份必须使用角色卡的稳定主名；别名只写进 aliases。不得在清单或候选名中使用“特昂糖（陆糖）”“爷爷（陆家老爷子）”这类组合展示名，也不得在主名、昵称、称谓之间交替指代同一角色。
- 每个出场角色输出 character_state_update。先读完整卡，只提交本章有依据的状态字段；未变化或未交代字段省略并保留旧值，不得用概括或占位值覆盖年龄、外貌、物品。无变化时逐字沿用一项已读状态。age、appearance 仅在正文确认变化时提交；修改已有值时分别用 `age_before`、`appearance_before` 逐字复制当前值，并用 `age_evidence`、`appearance_evidence` 提交本章正文中的逐字摘录。电话或消息直接参与只决定 chapter_link 的出场类型，不能证明参与者身处通话另一端的场景；正文未明确其实时地点时省略 current_location，保留旧值。
- items_or_assets 是整字段替换，不是自动追加；空字符串不清除旧值。已有非空值且本章确需更新时，必须同时提交 `items_or_assets_before`，逐字复制当前完整值；`items_or_assets` 新值必须逐字包含该完整旧值，再追加取得、转交、归还、遗失或销毁状态。自动建档禁止删除旧记录；确需删除由作者在角色编辑中复核。不能用本章短列表覆盖仍有效的历史资产。每件物品必须确由该角色持有、控制或经手；同场另一人物拿取或归还的物件不能记到当前角色名下。
- 仅真正没有现存卡片的新角色用 character_create；创建不能隐式合并已有姓名或别名。稳定档案出现新信息才用 character_update，并携带从真实档案读取的 id；不存在或跨作品的 id 会失败，不会回退创建。更新只提交有依据的变化字段；一旦提交 personality、background 或 custom_system_prompt，该值必须是读取旧档后合并的完整字段，系统会直接替换，不能提交增量片段。已有角色修改 background 时必须用 `background_before` 逐字复制当前完整背景，新 background 必须逐字包含该完整旧值，只追加正文确认、会长期影响后文的稳定身份或经历；自动建档禁止改写、缩短或删除旧背景，确需重写由作者在角色编辑中复核。profile 只提交有新证据的键，未变化的稳定设定保留。完整 profile 含 core_motivation、inner_lack、core_belief、public_persona、hidden_persona、reveal_chapter、moral_taboo、voice、action_habit、trauma_trigger。不要为了让每个出场人物都有“新档案”而重写原有动机、揭示章节、声线或秘密；本章的行动和短期目标应进入状态/时间线。
- “神秘人影、陌生声音、黑影、蒙面人”等身份未确认的描述，只作为本章角色线索写入摘要、场景和 chapter_link；除非正文已经提供可持续使用的稳定档案，否则不要放入 character_profiles，也不要创建空白永久角色卡。
- “排期编辑、保管员、门卫、护士、路人”等未具名岗位、临时称谓和泛指人物，在正文没有给出姓名、可持续识别信息或与现有角色相同的明确证据时，只把其行动写入摘要、section 场景、地点或事件；不得创建角色卡、状态卡、角色关系、角色档案或角色章节关联，也不得列入 coverage_manifest.characters/character_profiles。不得因为两个章节都使用相同岗位称谓，就把他们合并为同一个既有角色；只有读取到现有角色的精确 id 且正文明确确认是同一人时，才能更新该角色。
- character_create/update 的 role_type 只能是 protagonist、supporting、antagonist、mentor、other 之一；“穿越者”“陆家三岁孙女”等身份描述应写入 background/age，禁止与“主角”一起拼入 role_type。
- 新设定或变化使用 worldbuilding_create、worldbuilding_update、worldbuilding_timeline；维度仅用 geography、history、factions、power_system、races、culture。当前作品已有世界观时，worldbuilding_create 必须带 identity_resolution={{decision:"create", reviewed_existing_ids:[逐字复制 worldbuilding_identity_review_required 中的全部 ID；该列表为空时至少复制完整标题索引中最接近的一个 ID], reason:"逐项说明为何不是这些旧条目的更新"}}；语义判断由模型完成，应用只校验已交付候选 ID 是否全部被审阅以及 ID、归属与状态，不替模型判断身份。事实的 canonical_title_hint 与选定 active 标题不同时，在该候选用 `source_fact_titles` 列出原事实标签，显式声明其归入这个精确 ID/title。既有设定若本章只是关键引用且没有变化，不要虚构 update，同标题 chapter_link 即可。
- worldbuilding_create 只用于具有独立身份、独立生命周期或状态、且未来可脱离现有条目单独变化的实体。既有流程的一步、操作视角、字段、校验方法或细化规则，必须更新该流程的规范卡或写其时间线，不能另建卡。把多张现有卡重新归组形成的“证据链”、集合、章节结论或阶段汇总，应写入章节摘要、叙事账本、章节关联或各规范卡的时间线，不得把该汇总本身创建为世界观实体。如果新信息可以在不改变既有条目身份和稳定标题的前提下追加到一张旧卡，就必须 update/timeline；仅声称“层级不同”不是有效的新建理由。identity_resolution.reason 必须说明该实体为何需要独立持续存在，以及合并到逐项审阅的旧卡为何会损害其真实身份。
- `coverage_manifest.characters` 中每个稳定角色都必须有同名独立 character_state_update；`coverage_manifest.worldbuilding` 中新增、变化、确认、受损、受限或被使用的设定必须有同标题 worldbuilding_create/update/timeline，既有且未变化的引用必须有同标题 chapter_link；`coverage_manifest.character_profiles` 中每个角色必须有同名 character_create/update；`coverage_manifest.relationships` 中每个关系必须有同端点、同类型的 character_relationship。系统按身份逐项核对，重复卡不能凑数，数量或身份不足时本章不会通过验收。
- 世界观清单、候选和 chapter_link 必须使用完全相同的稳定标题。不要把“系统”改写成“系统（无界面·无沟通·自行探索型）”；说明性后缀写入 content/description。
- 亲属、师徒、盟友、敌对、主从、利益合作、情感关系等，只要正文明确且影响后续写作，就必须进入 relationships 和 character_relationship；双方都必须列入 characters，并已有角色档案或先输出 character_create/update。同一有向角色对只能保留一个当前 relationship_type，不得把“调查合作”“合作/联合核查”等近义类型同时列入清单；若第一次清单误列多个，用 coverage_manifest_mode="replace" 纠正。地点、功法、组织、事件不能作为角色关系端点。
- 使用 chapter_link 记录角色、设定、大纲、地点、物品、事件、重要性和出场顺序；全章聚合 link 的 characters 中每个角色只出现一次，由模型选择一个 appearance_type。角色关联使用 `characters: [{{name:"角色名", appearance_type:"出场|提及|回忆"}}]`，由模型依据正文明确分类：当前场景行动或通过电话、消息直接参与为“出场”，只在档案、名单、谈话或函件收件人中出现为“提及”，只在回忆段落中出现为“回忆”；应用不得猜测。设定关联使用 `worldbuilding_titles: [设定标题]`，并填写 description，避免把章节关联误写成角色关系卡。

【候选缺项自动修复】
- 当用户消息包含“上一轮校验未通过”时，这是增量修复回合，本节规则优先于【输出】中的首次生成要求。系统会保留上一轮已经通过的候选；只补充错误信息明确指出的缺失身份，或重发上一轮解析失败、身份不一致或结构错误候选的修正版，不要删除、缩减、改写或重复已有正确卡片，也不要重发完整候选集。
- 如果上一轮误把同一身份的别名、近义词或说明性标题列成多个 coverage_manifest 实体，单独输出一条 `type="chapter_summary"`，设置 `coverage_manifest_mode="replace"`，并完整提供 scene_count、characters、worldbuilding、relationships、character_profiles 五个字段。替换后的 scene_count 必须与事实及原摘要一致；该调用不能夹带其他候选，不会覆盖已保存的摘要正文和叙事账本。事实中的 canonical_title_hint 只是检索提示，应结合编号、正文和 active 档案解析到一个精确 id/title，不能据此新建重复卡。
- 聚合 chapter_link 有错误旧值时，单独提交 `chapter_link_mode="replace"`，完整提供 characters、worldbuilding_titles、locations、items、events 五个数组；它替换同一条候选。仅缺项时仍用普通增补。
- chapter_summary 和 chapter_outline 仅在错误信息明确指出缺失时输出；已通过其中任何一个时，都不得为了凑“完整骨架”而重复输出。
- 结束前逐项核对 coverage_manifest 与候选的名称和数量。
- 错误指出“新角色缺少可落库的角色资料候选”或“角色关系引用了没有资料卡的角色”时，只输出同名 character_create/update；系统会把有效资料卡合入上一轮保留的 character_profiles，不要重发 chapter_summary。错误信息给出“缺少角色状态候选、缺少世界观候选、缺少角色资料候选、缺少关系候选、缺少章节关联”时，必须逐个补齐，不要只重新输出摘要。
- 如果输出很长，优先保证清单中每个身份都有对应候选，再补充非必需时间线与说明；不得在已经声明完整清单后提前结束。

【section 场景】
每个独立场景都必须有一条 section 候选，并包含 scene_number、purpose、location、timeline、pov_character、characters、entry_state、exit_state、emotional_residue、unresolved_actions；没有内容的字段用空数组或明确的“未发生变化”，不得省略整张场景卡。

【叙事账本】
所有叙事变化都写进唯一一条 chapter_summary 的 narrative_state：已完成事件写 events，已揭示线索写 reader_known_facts，新增伏笔/承诺写 foreshadowing_planted，故事线进展写 storyline_progress，未完成行动写 unresolved_actions。不得把 completed_beat、revealed_clue、narrative_promise、storyline_state 当成顶层候选 type 单独输出。
每个条目记录稳定身份、状态、首次章节、最近章节、证据和置信度。每条 foreshadowing_planted、storyline_progress、unresolved_actions 的 evidence 必须是当前章节可逐字检索的 6-120 字原文摘录，禁止只写概述；找不到原文摘录就不生成该治理条目。低置信或无法匹配的内容保留待审，不强行合并。
解决伏笔、因果项或叙事债务时必须引用已有治理项的 resolves_item_id 或 resolves_dedupe_key；找不到稳定引用时保留待复核，不得按标题猜测关闭。

【判断边界】
- 只保留影响后续连续性的事件、状态、关系、设定、承诺、线索和故事线，不复述普通动作流水账。
- 中文小说必须用中文建档，不要改成英文或拼音。年龄是描述性文本；不确定内容明确标注，不把推测写成事实。
- 持久化背景、设定和时间线中的事件须注明实际发生章节或明确时间，不累积脱离来源的“本章、今天、明天”。背景只更新稳定经历或身份的新信息；日常行动写状态/时间线，不把每章经过不断追加成背景。未知年龄保持未知，不凭外貌补出年龄。
