# 项目管理指南

本文规定司命当前 3.3.x 代码线的开发、修复、验证和发布方式。目标是让每次变更都有明确用户价值、唯一业务路径、可复现验证和可追踪发布记录。

## 优先级

| 优先级 | 含义 | 示例 |
| --- | --- | --- |
| P0 | 阻断核心工作流或可能损坏数据 | 作品无法打开、章节被覆盖、建档写错实体、同步静默丢失分支 |
| P1 | 主要功能不可用或发布受阻 | 模型路由错误、任务无法恢复、安装包或 APK 无法启动 |
| P2 | 明显影响体验但存在安全替代路径 | 错误提示不清、上下文选择不可见、手机端明确降级 |
| P3 | 增强、重构、文档和维护 | 新模型适配、超大模块拆分、贡献指南 |

P0/P1 修复必须优先验证数据兼容、幂等、取消、恢复和跨端影响，不能只验证单个界面上的成功路径。

## Issue 规则

每个 issue 至少回答：

1. 用户要完成什么任务？
2. 当前行为和期望行为分别是什么？
3. 影响哪些入口：PC、Android、Gateway、API、CLI 或 MCP？
4. 是否涉及数据库迁移、文件镜像、同步、凭据或付费模型调用？
5. 用什么证据证明问题已解决？

建议标签：

- `bug`、`enhancement`、`docs`、`testing`、`release`
- `cataloging`、`model-routing`、`local-models`
- `external-agent`、`gateway`、`android`、`sync`

## 分支与 PR

长期分支和发布流以 [分支策略](branching-strategy.md) 为准：普通变更从 `develop` 创建 `feature/*`、`fix/*` 或 `refactor/*`，发布通过 `release/X.Y.Z` 进入 `main`，紧急补丁使用 `hotfix/*`。

示例：

```text
fix/cataloging-candidate-validation
feature/context-manifest-audit
refactor/workspace-assistant-stream
release/3.3.2
```

PR 描述至少包含：

- 改了什么以及为什么改。
- 用户可见影响和不在本次范围内的内容。
- 数据兼容、权限、隐私、迁移和回滚风险。
- PC/Android/Gateway 是否需要同步变更。
- 实际执行的验证命令与结果；未执行项必须说明原因。

## 分支保护

- `main` 只接收通过完整发布验收的 release/hotfix PR，禁止 force push 和删除。
- `develop` 接收日常集成 PR，同样禁止 force push，并要求相关架构、后端、前端或 Android CI 通过。
- `main` 和 `develop` 均要求 PR；个人维护时 approvals 可设为 0 或 1，但不能绕过失败检查。
- 正式 tag 必须是 `vX.Y.Z`，精确指向已通过 Release Gate 的提交，不能移动或复用。
- Release 分支合入 `main` 并发布后，应将最终状态同步回 `develop`。

## 开发检查清单

### 所有业务变更

- [ ] 只保留一个现行业务路径，废弃实现、引用、测试和文档在同次变更中删除。
- [ ] 用户最新明确消息是本轮意图真源；界面选中项和历史对话不能暗中覆盖它。
- [ ] PC 与 Android 的共有功能检查输入校验、权限、状态、写入、副作用和错误语义。
- [ ] 新增或修改写入时检查事务、幂等、并发、文件镜像、索引和同步投影。

### 后端

- [ ] 数据库字段变化包含 Alembic 迁移、旧数据升级和恢复演练。
- [ ] 路由不直接访问 ORM、不直接提交事务，也不直接选择具体模型适配器。
- [ ] 长任务覆盖运行、心跳、取消、断流、重试和应用重启。
- [ ] 异常模型输出、工具协议中断和空响应会明确失败，不使用静默 fallback。

### 前端

- [ ] loading、error、empty、disabled 和完成状态完整。
- [ ] 删除、覆盖、批量应用和正式保存前提供与风险相称的确认。
- [ ] 迟到请求不会覆盖未保存章节草稿或更新后的页面状态。
- [ ] 检查窄屏、小窗口、键盘导航和读屏标签。

### Agent、Prompt 与工具

- [ ] 自然语言意图与实体选择交给当前模型结合真实数据判断，不新增关键词、正则或启发式路由。
- [ ] 工具加入唯一注册表、宽粒度类别、权限包和前端/Android 投影。
- [ ] PromptSpec、ToolSpec、生成 OpenAPI 类型和移动端导出契约保持同步。
- [ ] 生成新章只产生未保存草稿；保存、建档和继续下一章仍由作者决定。
- [ ] 日志、错误、SSE 事件和诊断不会泄露 API Key、令牌或小说正文之外的本机秘密。

## 发布检查清单

### 版本与源码

- [ ] `backend/app/version.py`、`frontend/package.json`、Android `versionName/versionCode` 与 `vX.Y.Z` tag 一致。
- [ ] `docs/release-notes-X.Y.Z.md`、README 当前版本说明和相关专业文档已同步。
- [ ] Release 提交来自干净的 `release/X.Y.Z`，并已合入 `main`。
- [ ] 生成 OpenAPI 类型、移动端 Prompt/上下文资产和跨端能力文档没有漂移。

### 自动验证

- [ ] 后端 architecture/style gates 与完整测试通过。
- [ ] 前端 lint、单元测试、架构/重复检查、生成 API 检查、构建和关键路径 E2E 通过。
- [ ] Android 单元测试、lint、Debug 构建与签名 Release 构建通过。
- [ ] 性能基线、Windows 安装冒烟和 Gateway amd64/arm64 镜像冒烟通过。
- [ ] 新建、导入、建档、生成草稿、保存、失败恢复和至少一种 API/CLI 模型路径完成产品冒烟。

### 正式资产

- [ ] `build-installer.bat` 生成 `release/Siming-Setup.exe` 与 `release/Siming-Setup.sha256`。
- [ ] Android 发布脚本生成 `release/Siming.apk` 与 `release/Siming-apk-sha256.txt`。
- [ ] Release 中只上传上述四项资产；不上传旧单文件 `Siming.exe`、`update.json` 或 `sha256.txt`。
- [ ] Windows 安装包 SHA-256 与发布清单一致；配置证书后还必须通过 Authenticode 签名和时间戳验证。
- [ ] 安装冒烟确认安装目录内存在 `Siming.exe`、`.siming-installed` 与卸载器，且覆盖安装不会遗留已删除运行时文件。
- [ ] Gateway 发布同一版本的 `<version>`、`<major.minor>`、`latest` 三组标签，并包含 amd64/arm64、SBOM 与 provenance。

既有版本若经维护者明确授权只刷新 Android 安装包，`vX.Y.Z` tag 仍保持不可移动。必须从已通过 CI 的 `main` 提交手动运行 `Android Release Refresh`，确认 Android `versionName` 与目标 tag 一致，并保留 Artifact 中的源提交 provenance；发布时只覆盖 `Siming.apk` 与 `Siming-apk-sha256.txt`，Windows 两项资产及其摘要不得变化。覆盖后必须重新下载四项资产，复核 APK 签名、版本、SHA-256，并比对 Windows 资产 ID/大小/摘要与操作前基线。

### 发布后

- [ ] 从官方 Release 实际下载四项资产并重新核对 SHA-256。
- [ ] 验证一次全新 Windows 安装、一次旧安装版升级和一次历史单 EXE 数据目录迁移。
- [ ] 在非关键作品上验证 Gateway 配对、Android 离线编辑、冲突处理和升级后同步。
- [ ] 发布完成后删除 release 分支，将 `main` 最终状态同步回 `develop`。

## 每周维护节奏

1. 关闭已完成或重复 issue，把新反馈归类到 P0–P3。
2. 检查 CI、依赖告警、失败发布和仍未恢复的长任务问题。
3. 为下一迭代选择不超过三个可独立验收的主目标。
4. 对照版本真源检查 README、专业文档、生成契约和发布资产描述。
5. 记录本周最容易复发的问题及其自动化防线。

当前工程优先级见 [路线图](roadmap.md)，正式构建细节见 [Windows 安装与发布](../PACKAGING.md)。
