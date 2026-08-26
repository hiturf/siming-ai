import { act, renderHook, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { PropsWithChildren } from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const mockGet = vi.hoisted(() => vi.fn())
const mockPut = vi.hoisted(() => vi.fn())
const mockDelete = vi.hoisted(() => vi.fn())

vi.mock('../api/client', () => ({
  apiClient: { delete: mockDelete, get: mockGet, put: mockPut },
}))

import { useModelOptions } from '../hooks/useModelOptions'
import { useGlobalModelActions } from '../shared/query/modelConfigs'

function createWrapper() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  return function Wrapper({ children }: PropsWithChildren) {
    return <QueryClientProvider client={client}>{children}</QueryClientProvider>
  }
}

describe('useModelOptions readiness filtering', () => {
  beforeEach(() => vi.clearAllMocks())

  it('exposes only verified ready models and keeps the ready global default', async () => {
    mockGet.mockResolvedValue({ data: { data: { items: [
      {
        id: 'claude-detected', provider: 'claude_cli', default_model: 'claude-cli',
        is_global_default: false, readiness_status: 'detected', is_usable: false,
      },
      {
        id: 'opencode-ready', provider: 'opencode_cli', default_model: 'opencode/free-model',
        is_global_default: true, readiness_status: 'ready', is_usable: true,
      },
    ], total: 2 } } })

    const { result } = renderHook(() => useModelOptions(), { wrapper: createWrapper() })

    await waitFor(() => expect(result.current.loading).toBe(false))
    expect(result.current.modelOptions).toHaveLength(1)
    expect(result.current.modelOptions[0].value).toBe('opencode_cli:opencode/free-model')
    expect(result.current.defaultModel).toBe('opencode_cli:opencode/free-model')
    expect(result.current.hasDetectedModels).toBe(true)
  })

  it('does not fall back to the first detected CLI', async () => {
    mockGet.mockResolvedValue({ data: { data: { items: [{
      id: 'claude-detected', provider: 'claude_cli', default_model: 'claude-cli',
      is_global_default: false, readiness_status: 'detected', is_usable: false,
    }], total: 1 } } })

    const { result } = renderHook(() => useModelOptions(), { wrapper: createWrapper() })

    await waitFor(() => expect(result.current.loading).toBe(false))
    expect(result.current.modelOptions).toEqual([])
    expect(result.current.defaultModel).toBeUndefined()
    expect(result.current.hasModels).toBe(false)
    expect(result.current.hasDetectedModels).toBe(true)
  })

  it('exposes every discovered provider model and applies the task default', async () => {
    mockGet.mockResolvedValue({ data: { data: {
      items: [{
        id: 'openai-ready', provider: 'openai', default_model: 'gpt-4o',
        available_models: [
          { id: 'gpt-4o', display_name: 'GPT 4o' },
          { id: 'gpt-4.1-mini', display_name: 'GPT 4.1 Mini' },
        ],
        is_global_default: true, readiness_status: 'ready', is_usable: true,
      }],
      task_models: {
        writing: {
          task_type: 'writing', provider: 'openai', model: 'gpt-4.1-mini', is_usable: true,
        },
      },
    } } })

    const { result } = renderHook(() => useModelOptions('writing'), { wrapper: createWrapper() })

    await waitFor(() => expect(result.current.loading).toBe(false))
    expect(result.current.modelOptions.map((option) => option.value)).toEqual([
      'openai:gpt-4o',
      'openai:gpt-4.1-mini',
    ])
    expect(result.current.globalModel).toBe('openai:gpt-4o')
    await waitFor(() => expect(result.current.taskModel).toBe('openai:gpt-4.1-mini'))
    expect(result.current.defaultModel).toBe('openai:gpt-4.1-mini')
  })

  it('persists and clears the model for its declared task family', async () => {
    mockGet.mockResolvedValue({ data: { data: { items: [{
      id: 'openai-ready', provider: 'openai', default_model: 'gpt-4o',
      available_models: [{ id: 'gpt-4.1-mini', display_name: 'GPT 4.1 Mini' }],
      is_global_default: true, readiness_status: 'ready', is_usable: true,
    }] } } })
    mockPut.mockResolvedValue({ data: { data: {
      task_type: 'writing', provider: 'openai', model: 'gpt-4.1-mini', is_usable: true,
    } } })
    mockDelete.mockResolvedValue({ data: { data: null } })
    const { result } = renderHook(() => useModelOptions('writing'), { wrapper: createWrapper() })
    await waitFor(() => expect(result.current.modelOptions).toHaveLength(2))

    await act(async () => {
      await result.current.setTaskModel('openai:gpt-4.1-mini')
    })
    expect(mockPut).toHaveBeenCalledWith('/config/task-models/writing', {
      provider: 'openai',
      model: 'gpt-4.1-mini',
      context_length: null,
    })
    await waitFor(() => expect(result.current.taskModel).toBe('openai:gpt-4.1-mini'))

    await act(async () => {
      await result.current.setTaskModel(undefined)
    })
    expect(mockDelete).toHaveBeenCalledWith('/config/task-models/writing')
    await waitFor(() => expect(result.current.defaultModel).toBe('openai:gpt-4o'))
  })

  it('switches only to a ready model and synchronizes every mounted consumer', async () => {
    mockGet.mockResolvedValue({ data: { data: { items: [
      {
        id: 'opencode-ready', provider: 'opencode_cli', default_model: 'opencode/free-model',
        is_global_default: true, readiness_status: 'ready', is_usable: true,
      },
      {
        id: 'openai-ready', provider: 'openai', default_model: 'gpt-5-mini',
        is_global_default: false, readiness_status: 'ready', is_usable: true,
      },
      {
        id: 'claude-unverified', provider: 'claude_cli', default_model: 'claude-cli',
        is_global_default: false, readiness_status: 'unverified', is_usable: false,
      },
    ], total: 3 } } })
    mockPut.mockResolvedValue({ data: { data: { provider: 'openai', model: 'gpt-5-mini' } } })

    const { result } = renderHook(() => ({
      assistant: useModelOptions(),
      creation: useModelOptions(),
    }), { wrapper: createWrapper() })

    await waitFor(() => expect(result.current.assistant.modelOptions).toHaveLength(2))
    expect(result.current.assistant.modelOptions.some((option) => option.provider === 'claude_cli')).toBe(false)

    await act(async () => {
      await result.current.assistant.setGlobalModel('openai:gpt-5-mini')
    })

    expect(mockPut).toHaveBeenCalledWith('/config/global-model', {
      provider: 'openai',
      model: 'gpt-5-mini',
    })
    expect(result.current.assistant.defaultModel).toBe('openai:gpt-5-mini')
    expect(result.current.creation.defaultModel).toBe('openai:gpt-5-mini')
  })

  it('updates the model value when switching within the current provider', async () => {
    mockGet.mockResolvedValue({ data: { data: { items: [
      {
        id: 'local-ready', provider: 'local_llama_cpp', default_model: 'old-model.gguf',
        is_global_default: true, readiness_status: 'ready', is_usable: true,
      },
    ], total: 1 } } })
    mockPut.mockResolvedValue({ data: { data: { provider: 'local_llama_cpp', model: 'new-model.gguf' } } })

    const { result } = renderHook(() => ({
      options: useModelOptions(),
      actions: useGlobalModelActions(),
    }), { wrapper: createWrapper() })
    await waitFor(() => expect(result.current.options.defaultModel).toBe('local_llama_cpp:old-model.gguf'))

    await act(async () => {
      await result.current.actions.setGlobalModel('local_llama_cpp', 'new-model.gguf')
    })

    expect(result.current.options.defaultModel).toBe('local_llama_cpp:new-model.gguf')
    expect(result.current.options.modelOptions).toEqual(expect.arrayContaining([
      expect.objectContaining({
        provider: 'local_llama_cpp',
        model: 'new-model.gguf',
        isGlobalDefault: true,
      }),
    ]))
  })

  it('keeps the previous global model when the update request fails', async () => {
    mockGet.mockResolvedValue({ data: { data: { items: [
      {
        id: 'opencode-ready', provider: 'opencode_cli', default_model: 'opencode/free-model',
        is_global_default: true, readiness_status: 'ready', is_usable: true,
      },
      {
        id: 'openai-ready', provider: 'openai', default_model: 'gpt-5-mini',
        is_global_default: false, readiness_status: 'ready', is_usable: true,
      },
    ], total: 2 } } })
    mockPut.mockRejectedValue(new Error('保存失败'))
    const { result } = renderHook(() => useModelOptions(), { wrapper: createWrapper() })
    await waitFor(() => expect(result.current.defaultModel).toBe('opencode_cli:opencode/free-model'))

    await expect(result.current.setGlobalModel('openai:gpt-5-mini')).rejects.toThrow('保存失败')
    expect(result.current.defaultModel).toBe('opencode_cli:opencode/free-model')
  })
})
