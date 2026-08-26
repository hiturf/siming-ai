import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  type ModelTaskType,
  type SharedModelConfig,
  useGlobalModelActions,
  useSharedModelConfigs,
  useTaskModelActions,
} from '../shared/query/modelConfigs'

export type ModelConfig = SharedModelConfig

export interface ModelSelectOption {
  value: string
  label: string
  provider: string
  model: string
  isGlobalDefault: boolean
}

const PROVIDER_LABEL_MAP: Record<string, string> = {
  openai: 'OpenAI',
  anthropic: 'Anthropic Claude',
  deepseek: 'DeepSeek',
  qwen: '通义千问',
  gemini: 'Google Gemini',
  claude_cli: 'Claude Code CLI',
  codex_cli: 'Codex CLI',
  opencode_cli: 'opencode CLI',
  mimocode_cli: 'MiMo Code CLI',
  cursor_cli: 'Cursor Agent CLI',
  kilocode_cli: 'Kilo Code CLI',
  qwen_code_cli: 'Qwen Code CLI',
  hermes_cli: 'Hermes Agent CLI',
  openclaw_cli: 'OpenClaw CLI',
  dsh_cli: 'DeepSeek Harness CLI',
  custom_cli: '自定义本机 CLI',
  local_llama_cpp: '司命本地 AI',
}

const modelValue = (provider: string, model: string) => `${provider}:${model}`

const normalizeModel = (provider: string, model: string) => {
  if (provider === 'deepseek' && model === 'deepseek-v3') {
    return 'deepseek-v4-flash'
  }
  if (provider === 'gemini' && model.startsWith('models/')) {
    return model.slice('models/'.length)
  }
  return model
}

export function useModelOptions(taskType?: ModelTaskType) {
  const configsQuery = useSharedModelConfigs()
  const { setGlobalModel: persistGlobalModel } = useGlobalModelActions()
  const {
    setTaskModel: persistTaskModel,
    clearTaskModel: persistTaskModelClear,
  } = useTaskModelActions()
  const configs = useMemo(() => configsQuery.data?.items || [], [configsQuery.data?.items])
  const [selectionOverride, setSelectionOverride] = useState<string>()

  useEffect(() => {
    const handleSelection = (event: Event) => {
      const value = (event as CustomEvent<string>).detail
      if (value) setSelectionOverride(value)
    }
    window.addEventListener('siming:global-model-changed', handleSelection)
    return () => window.removeEventListener('siming:global-model-changed', handleSelection)
  }, [])

  const effectiveConfigs = useMemo(() => {
    if (!selectionOverride) return configs
    const separator = selectionOverride.indexOf(':')
    if (separator <= 0) return configs
    const provider = selectionOverride.slice(0, separator)
    const model = selectionOverride.slice(separator + 1)
    return configs.map((config) => ({
      ...config,
      default_model: config.provider === provider ? model : config.default_model,
      is_global_default: `${config.provider}:${config.default_model}` === selectionOverride
        || config.provider === provider,
    }))
  }, [configs, selectionOverride])

  const modelOptions = useMemo<ModelSelectOption[]>(() => {
    const options = effectiveConfigs
      .filter((config) => config.is_usable && config.readiness_status === 'ready')
      .flatMap((config) => {
        const defaultModel = normalizeModel(config.provider, config.default_model)
        const available = new Map<string, string>()
        available.set(defaultModel, defaultModel)
        for (const candidate of config.available_models || []) {
          const model = normalizeModel(config.provider, String(candidate.id || '').trim())
          if (model) available.set(model, String(candidate.display_name || model))
        }
        return Array.from(available.entries()).map(([model, displayName]) => {
          const isGlobalDefault = Boolean(
            config.is_global_default && model === defaultModel,
          )
          return {
            value: modelValue(config.provider, model),
            label: `${PROVIDER_LABEL_MAP[config.provider] || config.provider} · ${displayName}${isGlobalDefault ? '（全局默认）' : ''}`,
            provider: config.provider,
            model,
            isGlobalDefault,
          }
        })
      })
    return Array.from(
      new Map(options.map((option) => [option.value, option])).values(),
    )
  }, [effectiveConfigs])

  const globalModel = useMemo(
    () => modelOptions.find((option) => option.isGlobalDefault)?.value,
    [modelOptions],
  )
  const taskSetting = taskType ? configsQuery.data?.task_models?.[taskType] : undefined
  const taskModel = useMemo(() => {
    if (!taskSetting || taskSetting.is_usable === false) return undefined
    const value = modelValue(
      taskSetting.provider,
      normalizeModel(taskSetting.provider, taskSetting.model),
    )
    return modelOptions.some((option) => option.value === value) ? value : undefined
  }, [modelOptions, taskSetting])
  const defaultModel = taskModel || globalModel

  const detectedConfigs = useMemo(
    () => configs.filter((config) => !config.is_usable),
    [configs],
  )

  const setGlobalModel = useCallback(async (value: string) => {
    const option = modelOptions.find((candidate) => candidate.value === value)
    if (!option) throw new Error('所选模型尚未通过真实对话测试')
    await persistGlobalModel(option.provider, option.model)
    if (typeof window !== 'undefined') {
      window.dispatchEvent(new CustomEvent('siming:global-model-changed', { detail: value }))
    }
    return option
  }, [modelOptions, persistGlobalModel])

  const setTaskModel = useCallback(async (value?: string, contextLength?: number | null) => {
    if (!taskType) throw new Error('当前入口没有声明任务类型')
    if (!value) {
      await persistTaskModelClear(taskType)
      return undefined
    }
    const option = modelOptions.find((candidate) => candidate.value === value)
    if (!option) throw new Error('所选模型尚未通过真实对话测试')
    return persistTaskModel(
      taskType,
      option.provider,
      option.model,
      contextLength,
    )
  }, [modelOptions, persistTaskModel, persistTaskModelClear, taskType])

  return {
    configs,
    modelOptions,
    defaultModel,
    globalModel,
    taskModel,
    loading: configsQuery.isLoading || configsQuery.isFetching,
    refresh: configsQuery.refetch,
    setGlobalModel,
    setTaskModel,
    hasModels: modelOptions.length > 0,
    hasDetectedModels: detectedConfigs.length > 0,
    detectedConfigs,
  }
}
