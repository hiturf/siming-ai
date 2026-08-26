import { describe, expect, it } from 'vitest'
import type { OperationRun } from '../shared/api/contracts'
import { projectCatalogingMessages } from '../components/assistant/catalogingNotifications'

function operation(overrides: Partial<OperationRun> = {}): OperationRun {
  return {
    id: 'operation-1',
    source_kind: 'cataloging',
    source_id: 'job-1',
    project_id: 'project-1',
    title: '《第二章 吐纳》章节建档',
    status: 'running',
    health_status: 'active',
    can_pause: true,
    can_cancel: true,
    can_retry: true,
    elapsed_seconds: 0,
    progress: { mode: 'determinate', current: 0, total: 1 },
    tool_mode: 'chapter_save:internal_llm',
    created_at: '2026-08-12T10:00:00Z',
    updated_at: '2026-08-12T10:00:01Z',
    ...overrides,
  }
}

describe('projectCatalogingMessages', () => {
  it('announces the hard writing fence after the author starts cataloging', () => {
    const messages = projectCatalogingMessages([operation()], 'project-1')

    expect(messages).toHaveLength(1)
    expect(messages[0].content).toContain('建档已经开始')
    expect(messages[0].content).toContain('下一章写作已锁定')
    expect(messages[0].navigation_action).toEqual({
      label: '查看建档进度',
      to: '/project/project-1?view=cataloging',
    })
  })

  it('adds a durable completion notification', () => {
    const messages = projectCatalogingMessages([
      operation({
        status: 'completed',
        completed_at: '2026-08-12T10:00:20Z',
        result_summary: '作品建档完成，共处理 1 章',
      }),
    ], 'project-1')

    expect(messages).toHaveLength(1)
    expect(messages[0].content).toContain('建档已完成')
    expect(messages[0].content).toContain('现在可以继续生成下一章')
    expect(messages[0].data?.outcome).toBe('completed_with_tools')
    expect(messages[0].navigation_action?.label).toBe('查看建档结果')
  })

  it('does not leak another project or manual cataloging task into chat', () => {
    const messages = projectCatalogingMessages([
      operation({ project_id: 'project-2' }),
      operation({ id: 'manual', tool_mode: 'internal_llm' }),
    ], 'project-1')

    expect(messages).toEqual([])
  })

  it('reports an incomplete task as blocked instead of success', () => {
    const messages = projectCatalogingMessages([
      operation({ status: 'paused', next_action: '缺少角色关系候选' }),
    ], 'project-1')

    expect(messages[0].status).toBe('error')
    expect(messages[0].content).toContain('缺少角色关系候选')
    expect(messages[0].data?.outcome).toBe('blocked')
    expect(messages[0].navigation_action?.label).toBe('前往处理建档')
  })

  it('uses the operation action URL when the backend provides one', () => {
    const messages = projectCatalogingMessages([
      operation({
        status: 'waiting_user',
        attention: {
          action_label: '处理候选',
          action_url: '/project/project-1?view=cataloging&job=job-1',
        },
      }),
    ], 'project-1')

    expect(messages[0].navigation_action).toEqual({
      label: '前往处理建档',
      to: '/project/project-1?view=cataloging&job=job-1',
    })
  })

  it('orders the live reminder by latest task activity so it follows the writer reply', () => {
    const messages = projectCatalogingMessages([
      operation({
        created_at: '2026-08-12T10:00:00Z',
        updated_at: '2026-08-12T10:00:30Z',
      }),
    ], 'project-1')

    expect(messages[0].created_at).toBe('2026-08-12T10:00:30Z')
  })

  it('does not repeat the backend running notice inside the synthesized message', () => {
    const messages = projectCatalogingMessages([
      operation({ current_message: '《第二章 吐纳》已保存，作者已启动建档。' }),
    ], 'project-1')

    expect(messages[0].content).not.toContain('作者已启动建档')
  })
})
