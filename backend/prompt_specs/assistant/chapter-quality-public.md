---
id: assistant.chapter.quality.public
version: 3.1.0
scope: chapter_writing
visibility: public
inputs: [style_context]
output_format: prose
tool_policy: governed_external
tools: [save_external_chapter_draft]
fragments: [assistant.chapter.quality, shared.execution-contract]
budget:
  fixed_chars: 7600
  context_chars: 12000
golden_cases:
  - name: external-quality-contract
    required_text: ["API-free 模式", "未入库草稿", "立即结束", "作者"]
---
【API-free 模式】
- 只生成一次基础正文；不得调用未提供的工具或追加分析、评审、改写轮次。
- 目标必须是尚未关联正式章节的章级大纲；已有正式章节不能作为新章草稿目标，也不得通过草稿覆盖。
- 长正文只调用 save_external_chapter_draft 保存为未入库草稿；工具成功后立即结束本轮，不得再调用任何正式章节、衍生档案或建档工具，也不要直接写 chapters/*.md 冒充完成。
- 草稿会载入作者的正文编辑器。正式保存和是否启动建档由作者点击“保存并建档”或“仅保存”决定；建档完成前不得继续生成下一章。
