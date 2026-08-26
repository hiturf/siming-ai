# Siming MCP 现行架构

> 协议版本：MCP `2024-11-05`
> 传输：stdio
> 权威实现：`backend/app/mcp/server.py`、`backend/app/services/workspace/registry.py`

## 1. 单一工具来源

MCP、项目助手 API、立项 Agent 和手机独立 Agent 共用同一套业务工具契约：

- `ToolRegistry` 保存工具名称、参数结构、处理器、权限类型和 Agent 类别。
- `backend/app/architecture/tool_categories.py` 是宽粒度工具类别的唯一目录。
- 每个已注册业务工具必须且只能属于一个类别；启动校验会拒绝漏分或重复分配。
- MCP 不维护另一份业务工具表，也不解析模型输出中的 JSON 指令来模拟工具调用。

## 2. 模型控制的类别开放

司命启动 API 或 CLI Agent 回合时，首个模型步骤只开放 `set_tool_categories`：

1. 模型根据用户最新消息决定完成任务需要哪些能力类别。
2. 模型调用 `set_tool_categories(enabled_categories=[...])`，以完整替换语义设置下一步骤的类别；空数组关闭全部业务工具。
3. 控制器调用立即结束当前模型步骤。该步骤里夹带的其他工具调用全部失效。
4. 下一模型步骤只看到“所选类别 ∩ 当前权限包 ∩ 当前入口真实实现”的工具。
5. 需要换类时，模型再次调用控制器并进入新的模型步骤。

应用代码不会通过正则、关键词、界面选中项或固定短语替模型选择类别。类别控制器只接受结构化类别名，并不接受单个工具开关。

当前类别如下：

| 类别 | 能力范围 |
| --- | --- |
| `project_files` | 作品资料、项目文件、导入导出和写作统计 |
| `story_knowledge` | 大纲、章节、角色、关系和世界观实体 |
| `writing_context` | 写作上下文、正文或资料生成、未保存草稿 |
| `cataloging` | 建档任务、候选事实和状态控制 |
| `analysis_governance` | 质量、冲突、拆书和叙事治理 |
| `creation_data` | 立项会话、结构化资料、实体、依赖和字段锁 |
| `creation_flow` | 立项生成、确认、版本、任务、导入和正式建书 |
| `agent_runtime` | Agent 运行、进度、草稿缓冲、计划任务和记忆 |
| `extensions` | Skill、联网、MCP 指南、提示词包和质量规范 |

具体成员由代码目录生成，文档不复制工具清单，避免形成第二事实源。

## 3. MCP 回合状态

司命启动的本机 CLI 使用临时 `siming_turn` MCP，并传入仅本轮有效的类别状态文件：

- `tools/list` 初始只返回 `set_tool_categories`。
- 控制器写入“请求类别版本”，但不会在同一 CLI 模型步骤激活。
- 同一步骤继续调用其他工具会得到拒绝结果。
- 外层 Agent 观察到新版本后结束该 CLI 步骤、激活类别，并以新的 CLI 模型步骤继续。
- 审计记录保存控制器选择、工具参数和实际结果；CLI 退出后删除临时状态。

普通、非 Agent 回合启动的 MCP 若没有类别状态文件，则仍只按显式权限包列出工具；它不会获得隐式项目权限。

## 4. 权限与实体边界

类别不是授权。每次 `tools/list` 和 `tools/call` 都会再次执行确定性校验：

- permission pack / permission tier；
- 当前项目或立项会话归属；
- 实体 ID 是否存在、归属是否正确、类型是否可用；
- 写入事务、revision、幂等键和状态机；
- 章节草稿和建档的回合终止边界。

模型可以选择业务目标和工具，但不能通过选择类别扩大入口本身的权限。

## 5. 章节写作边界

- API 模型在 `writing_context` 类别中使用 `chapter_writer`。
- 本机 CLI 在同一类别中使用 `prepare_external_writing_context` 和 `save_external_chapter_draft`。
- 目标必须由 Agent 读取真实作品实体后提供章级 ID；界面当前章节不会暗中绑定到生成器。
- 新章节生成一份独立未保存草稿，不更新正式章节。
- 草稿成功即结束模型回合；作者随后选择“仅保存”或“保存并建档”。
- 建档完成前不得自动生成下一章。

## 6. JSON-RPC 与错误

服务器支持 `initialize`、`tools/list`、`tools/call`、`prompts/list`、`prompts/get` 和 `ping`。响应使用标准 JSON-RPC 2.0；工具业务结果放在 MCP text content 中，并包含 `tool`、`status`、`detail` 和可选 `data`。

协议错误使用以下代码：

| 代码 | 含义 |
| --- | --- |
| `-32700` | JSON 解析失败 |
| `-32600` | 请求格式无效 |
| `-32601` | 方法不存在 |
| `-32602` | 参数无效 |
| `-32603` | 内部错误 |
| `-32000` | 工具不存在 |
| `-32001` | 权限或类别拒绝 |
| `-32002` | 项目不存在 |
| `-32003` | 工具执行失败 |

错误不会触发隐藏 fallback、另一套工具桥或静默写入路径。

## 7. 安全要求

MCP 不得暴露 API Key、模型密钥、令牌、数据库连接串、项目范围外路径或内部隐藏推理。司命启动的临时 MCP 只绑定当前作品或立项会话，进程退出后失效，也不会修改 CLI 的全局 MCP 配置。
