# Siming 本地 AI 与 LoRA 训练

## 数据与目录

- 推理模型：`%LOCALAPPDATA%\Siming\models`
- llama.cpp：`%LOCALAPPDATA%\Siming\runtimes\llama_cpp`
- 训练环境：`%LOCALAPPDATA%\Siming\runtimes\trainer`
- 训练集、检查点与适配器：`%LOCALAPPDATA%\Siming\training`

这些目录可以与小说数据目录分开。模型目录可在桌面控制面板的“本地 AI”页面修改。

## 硬件分档

| 档位 | 初始推荐 | 初始上下文 | 适用设备 |
| --- | --- | --- | --- |
| 轻量 | Qwen3.5 4B UD-Q4_K_XL | 8K | CPU、低显存设备 |
| 标准 | Qwen3.5 9B UD-Q4_K_XL | 16K | 12GB 显存或较大内存 |
| 高质量 | Qwen3.8 27B UD-Q4_K_XL | 32K | 24GB 显存、32GB 及以上内存 |

这些只是首次选择的建议，不是上限。任务设置和手动加载都可以填写任意正数的上下文 token；司命会把该值原样交给 llama.cpp。若机器内存或显存不足，启动会保留 llama.cpp 的诊断并失败，不会静默缩短上下文后继续运行。

内置目录按当前官方开放尺寸提供 Qwen3.5 4B/9B 与 Qwen3.8 27B GGUF。Qwen3.8 当前开放的本地密集型号为 27B，因此轻量和标准档继续使用 Qwen3.5。模型中心也支持直接登记已有的任意 GGUF，或填写 GGUF 的 HTTPS 下载直链；登记本机文件不会复制、移动或在“移除登记”时删除源文件。

## 任务路由

系统设置会保存每个 API、CLI 和本地运行时获取到的完整模型目录，并可分别为以下任务指定其中任一模型：

- 项目助手
- 新书与大纲规划
- 作品建档
- 章节写作
- 质量评估
- 拆书分析

模型选择只有一条权威优先级：本次任务明确选择 > 任务默认 > 全局默认。任务默认可以跨 API、CLI 和本地模型设置；没有任务默认时才跟随全局默认。本地模型还可在同一任务设置中指定启动上下文长度，超过模型容量时会明确拒绝保存。

## 结构化输出

本地模型通过 llama.cpp 的 OpenAI 兼容接口接入 `LLMGateway`。项目 Agent 首个模型步骤只获得 `set_tool_categories`，由模型按最新消息选择下一步骤所需的宽粒度能力类别；运行时再与权限和真实工具注册表取交集。接口报错、工具协议中断或空工具流会如实停止并给出诊断，不会静默切换为另一条文本执行路径。要求 JSON 的独立任务会启用 JSON 模式，支持调用方传入 JSON Schema。

## 本机 Agent CLI

Claude Code、Codex CLI、OpenCode、Mimo Code、Cursor Agent、Kilo Code、Qwen Code、Hermes、OpenClaw 和 DeepSeek Harness（DSH）均使用由司命启动进程携带的临时 `siming_turn` MCP。司命会在启动时完成该 MCP 的授权，不再显示一次性授权按钮，也不再解析 CLI 输出中的 JSON 工具指令。MCP 与 API 使用同一工具类别目录和 `set_tool_categories` 步骤边界；临时 MCP 只绑定当前作品或立项，CLI 退出后失效，不修改 CLI 的全局 MCP 配置。

DSH 使用本机现有的登录、模型和 headless profile；司命通过 `--patch` 仅向本轮进程加入 `@deepseek-ai/dsh-mcp-client`。自定义 CLI 因启动协议未知，只能返回文本，不能用于立项或作品数据写入。

## LoRA 训练 Beta

首版训练仅支持 NVIDIA CUDA，建议：

- 4B QLoRA：8GB 及以上显存
- 9B QLoRA：12-16GB 及以上显存
- 27B QLoRA：24GB 及以上显存

训练流程：

1. 选择作品并生成训练集。
2. 检查样本数量、长度和训练/验证划分。
3. 确认拥有文本训练权利。
4. 选择基座、LoRA Rank、样本长度与训练轮数。
5. 训练任务支持暂停、继续、取消和检查点恢复。
6. PEFT 适配器转换为 llama.cpp GGUF LoRA 后登记到模型中心。
7. 新适配器默认停用，用户确认后再启用或设为写作默认。

## 远程清单

程序始终内置可离线使用的基础模型清单。发布方可以通过：

- `MOSHU_MODEL_MANIFEST_URL`
- `MOSHU_MODEL_MANIFEST_PUBLIC_KEY`

提供 Ed25519 签名的远程模型清单。签名验证失败时不会使用远程内容，并自动回退到上次验证成功的缓存或内置清单。
