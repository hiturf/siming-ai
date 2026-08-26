import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { MemoryRouter } from 'react-router-dom'
import { QueryClientProvider } from '@tanstack/react-query'
import { createSimingQueryClient } from '../shared/query/client'

const api = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), delete: vi.fn() }))
vi.mock('../api/client', () => ({ apiClient: api }))

import GlobalOperationCenter from '../features/operations/components/GlobalOperationCenter'

function renderCenter() {
  const client = createSimingQueryClient()
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter><GlobalOperationCenter /></MemoryRouter>
    </QueryClientProvider>,
  )
}

class FakeEventSource {
  static last: FakeEventSource | undefined
  readonly url: string
  onopen: ((event: Event) => void) | null = null
  onerror: ((event: Event) => void) | null = null
  listeners = new Map<string, (event: Event) => void>()

  constructor(url: string | URL) {
    this.url = String(url)
    FakeEventSource.last = this
  }

  addEventListener(type: string, listener: EventListenerOrEventListenerObject) {
    const callback = typeof listener === 'function' ? listener : listener.handleEvent.bind(listener)
    this.listeners.set(type, callback)
  }

  close() {}
}

const operation = {
  id: 'operation-1',
  source_kind: 'cataloging',
  title: '作品建档 · 第151章',
  status: 'running',
  health_status: 'suspected_stall',
  phase: 'chapter_archive',
  current_message: '正在检查第151章的角色状态',
  progress: { mode: 'indeterminate', current: null, total: null, percent: null },
  model_source: 'opencode_cli:opencode/big-pickle',
  next_action: '可以继续等待，或只重试当前章节',
  resume_url: '/project/project-1?view=cataloging',
  can_pause: true,
  can_cancel: true,
  can_retry: true,
  elapsed_seconds: 2234,
  last_activity_at: new Date().toISOString(),
}

describe('GlobalOperationCenter', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    localStorage.clear()
    FakeEventSource.last = undefined
    vi.stubGlobal('EventSource', FakeEventSource)
    api.get.mockResolvedValue({ data: { data: { items: [operation] } } })
    api.post.mockResolvedValue({ data: { data: operation } })
    api.delete.mockResolvedValue({ data: { data: {} } })
  })

  afterEach(() => vi.unstubAllGlobals())

  it('shows real health and avoids a fabricated percentage for indeterminate work', async () => {
    renderCenter()

    const trigger = await screen.findByRole('button', { name: /全局任务中心/ })
    fireEvent.click(trigger)

    expect(await screen.findByText('疑似停滞')).toBeInTheDocument()
    expect(screen.getByText('正在等待下一条真实活动，不估算完成百分比')).toBeInTheDocument()
    expect(screen.getByText(/^最近活动 \d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$/)).toBeInTheDocument()
    expect(screen.queryByText(/最近活动.*小时前/)).not.toBeInTheDocument()
    expect(screen.getByText(/opencode_cli:opencode\/big-pickle/)).toBeInTheDocument()
    expect(screen.queryByText(/\d+%/)).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: /重试当前单元/ }))
    await waitFor(() => expect(api.post).toHaveBeenCalledWith('/operations/operation-1/retry-current-unit'))
  })

  it('treats an SSE break as reconnection while polling remains active', async () => {
    renderCenter()
    fireEvent.click(await screen.findByRole('button', { name: /全局任务中心/ }))
    await waitFor(() => expect(FakeEventSource.last?.url).toBe('/api/v1/operations/operation-1/stream'))

    act(() => FakeEventSource.last?.onerror?.(new Event('error')))

    expect(await screen.findByText('进度流正在重新连接，已改用状态轮询')).toBeInTheDocument()
    expect(screen.getByText('作品建档 · 第151章')).toBeInTheDocument()
  })

  it('renders and refreshes the latest streamed model-output snapshot', async () => {
    const liveOperation = {
      ...operation,
      health_status: 'active',
      current_message: '模型正在生成 · 已输出 1,200 字',
      process_metrics: {
        kind: 'model_output',
        output_chars: 1200,
        output_preview: '正在整理第一章场景',
        attempt: 1,
      },
    }
    api.get.mockResolvedValue({ data: { data: { items: [liveOperation] } } })

    renderCenter()
    fireEvent.click(await screen.findByRole('button', { name: /全局任务中心/ }))
    expect(await screen.findByText('已输出 1,200 字')).toBeInTheDocument()
    expect(screen.getByText('正在整理第一章场景')).toBeInTheDocument()

    const heartbeat = FakeEventSource.last?.listeners.get('heartbeat')
    act(() => heartbeat?.(new MessageEvent('heartbeat', {
      data: JSON.stringify({
        ...liveOperation,
        current_message: '模型正在生成 · 已输出 2,400 字',
        process_metrics: {
          kind: 'model_output',
          output_chars: 2400,
          output_preview: '正在整理第二章场景',
          attempt: 1,
        },
      }),
    })))

    expect(await screen.findByText('已输出 2,400 字')).toBeInTheDocument()
    expect(screen.getByText('正在整理第二章场景')).toBeInTheDocument()
  })

  it('shows a persistent author action instead of a running spinner while waiting for confirmation', async () => {
    api.get.mockResolvedValue({
      data: {
        data: {
          items: [{
            ...operation,
            status: 'waiting_user',
            health_status: 'active',
            outcome: 'waiting_user',
            current_message: '文风与世界观已经生成',
            result_summary: '阶段内容已保存到立项草稿',
            result: {
              outcome: 'waiting_user',
              summary: '阶段内容已保存到立项草稿',
              completed: ['生成文风与世界观'],
              incomplete: ['作者确认'],
            },
            attention: {
              kind: 'confirmation',
              title: '阶段内容等待确认',
              message: '请审阅后确认。',
              action_label: '审阅阶段内容',
              action_url: '/novel-creation?session=session-1&stage=world_style',
            },
          }],
        },
      },
    })

    renderCenter()
    fireEvent.click(await screen.findByRole('button', { name: /全局任务中心/ }))

    expect(await screen.findByRole('heading', { name: /待你处理/ })).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: '正在运行' })).not.toBeInTheDocument()
    expect(await screen.findByText('阶段内容等待确认')).toBeInTheDocument()
    expect(screen.getByText('已完成：生成文风与世界观')).toBeInTheDocument()
    expect(screen.getByText('未完成：作者确认')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '审阅阶段内容' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '暂停' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '取消' })).not.toBeInTheDocument()
    expect(screen.queryByText('正在等待下一条真实活动，不估算完成百分比')).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: '全部标为已读' }))
    await waitFor(() => expect(screen.getByRole('button', { name: /全局任务中心，0 项未读提醒/ })).toBeInTheDocument())
    expect(screen.getByRole('button', { name: '全部标为已读' })).toBeDisabled()
    expect(screen.getByRole('heading', { name: '待你处理' })).toBeInTheDocument()
    expect(api.post).toHaveBeenCalledWith('/operations/attention/read', { operation_ids: ['operation-1'] })
    expect(localStorage.getItem('siming.operation-center.read-attention.v1')).toBeNull()
  })

  it('groups repeated attempts for the same source under one current task', async () => {
    api.get.mockResolvedValue({
      data: {
        data: {
          items: [
            { ...operation, id: 'operation-new', source_id: 'chapter-151', updated_at: '2026-07-27T12:00:00Z' },
            { ...operation, id: 'operation-old', source_id: 'chapter-151', status: 'failed', updated_at: '2026-07-27T10:00:00Z' },
          ],
        },
      },
    })

    renderCenter()
    fireEvent.click(await screen.findByRole('button', { name: /全局任务中心/ }))

    expect(await screen.findAllByText('作品建档 · 第151章')).toHaveLength(1)
    expect(screen.getByText('历史尝试 1')).toBeInTheDocument()
  })

  it('deletes a finished task and its grouped history only after confirmation', async () => {
    api.get.mockResolvedValue({
      data: {
        data: {
          items: [
            { ...operation, id: 'operation-new', source_id: 'chapter-151', status: 'completed', can_retry: false, updated_at: '2026-07-27T12:00:00Z' },
            { ...operation, id: 'operation-old', source_id: 'chapter-151', status: 'failed', can_retry: false, updated_at: '2026-07-27T10:00:00Z' },
          ],
        },
      },
    })

    renderCenter()
    fireEvent.click(await screen.findByRole('button', { name: /全局任务中心/ }))
    fireEvent.click(await screen.findByRole('button', { name: '删除任务记录：作品建档 · 第151章' }))

    expect(await screen.findByText('会删除当前记录及 1 次历史尝试，且无法撤销。')).toBeInTheDocument()
    fireEvent.click(within(screen.getByRole('tooltip')).getByRole('button', { name: /删\s*除/ }))

    await waitFor(() => {
      expect(api.delete).toHaveBeenNthCalledWith(1, '/operations/operation-new')
      expect(api.delete).toHaveBeenNthCalledWith(2, '/operations/operation-old')
    })
  })
})
