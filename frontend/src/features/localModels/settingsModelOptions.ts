export interface ModelOption {
  id: string
  display_name?: string
}

export type ModelDiscoveryState = {
  status: 'idle' | 'success' | 'manual'
  message?: string
}

export type ReadinessStatus =
  | 'detected'
  | 'unverified'
  | 'testing'
  | 'ready'
  | 'auth_required'
  | 'quota_limited'
  | 'unavailable'

export const PROVIDER_OPTIONS = [
  { value: 'openai', label: 'OpenAI' },
  { value: 'anthropic', label: 'Anthropic Claude' },
  { value: 'deepseek', label: 'DeepSeek（v4-pro / v4-flash）' },
  { value: 'qwen', label: '通义千问' },
  { value: 'gemini', label: 'Google Gemini' },
  { value: 'claude_cli', label: 'Claude Code CLI（本机）' },
  { value: 'codex_cli', label: 'Codex CLI（本机）' },
  { value: 'opencode_cli', label: 'opencode CLI（本机）' },
  { value: 'mimocode_cli', label: 'MiMo Code CLI（本机）' },
  { value: 'cursor_cli', label: 'Cursor Agent CLI（本机）' },
  { value: 'kilocode_cli', label: 'Kilo Code CLI（本机）' },
  { value: 'qwen_code_cli', label: 'Qwen Code CLI（本机）' },
  { value: 'hermes_cli', label: 'Hermes Agent CLI（本机）' },
  { value: 'openclaw_cli', label: 'OpenClaw CLI（本机）' },
  { value: 'dsh_cli', label: 'DeepSeek Harness CLI（本机）' },
  { value: 'custom_cli', label: '自定义本机 CLI' },
  { value: '__custom_openai_compatible__', label: '自定义 OpenAI 兼容' },
]

export const CUSTOM_PROVIDER_VALUE = '__custom_openai_compatible__'
export const PROVIDER_ID_PATTERN = /^[A-Za-z0-9_-]+$/

const PROVIDER_LABEL_MAP: Record<string, string> = {
  openai: 'OpenAI',
  anthropic: 'Anthropic Claude',
  deepseek: 'DeepSeek',
  qwen: '通义千问',
  gemini: 'Google Gemini',
  local_llama_cpp: '司命本地 AI',
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
}

const PROVIDER_COLOR_MAP: Record<string, string> = {
  openai: 'green',
  anthropic: 'purple',
  deepseek: 'blue',
  qwen: 'orange',
  gemini: 'cyan',
  local_llama_cpp: 'green',
  claude_cli: 'purple',
  codex_cli: 'geekblue',
  opencode_cli: 'magenta',
  mimocode_cli: 'gold',
  cursor_cli: 'blue',
  kilocode_cli: 'volcano',
  qwen_code_cli: 'cyan',
  hermes_cli: 'purple',
  openclaw_cli: 'green',
  dsh_cli: 'blue',
  custom_cli: 'default',
}

export const providerLabel = (provider?: string | null) => (
  provider ? PROVIDER_LABEL_MAP[provider] || provider : ''
)

export const providerColor = (provider?: string | null) => (
  provider ? PROVIDER_COLOR_MAP[provider] || 'default' : 'default'
)

export const isKnownProvider = (provider?: string | null) => Boolean(
  provider && PROVIDER_LABEL_MAP[provider],
)

export const isCustomProviderSelection = (provider?: string) => (
  provider === CUSTOM_PROVIDER_VALUE
)

const LOCAL_CLI_PROVIDERS = [
  'claude_cli',
  'codex_cli',
  'opencode_cli',
  'mimocode_cli',
  'cursor_cli',
  'kilocode_cli',
  'qwen_code_cli',
  'hermes_cli',
  'openclaw_cli',
  'dsh_cli',
  'custom_cli',
]

export const isLocalCliProvider = (provider?: string) => Boolean(
  provider && LOCAL_CLI_PROVIDERS.includes(provider),
)

export const resolveProviderForSubmit = (values: {
  provider?: string
  custom_provider?: string
}) => (
  isCustomProviderSelection(values.provider)
    ? String(values.custom_provider || '').trim()
    : values.provider
)

const DEEPSEEK_MODEL_OPTIONS: ModelOption[] = [
  { id: 'deepseek-v4-pro', display_name: 'deepseek-v4-pro' },
  { id: 'deepseek-v4-flash', display_name: 'deepseek-v4-flash' },
]

const GEMINI_MODEL_OPTIONS: ModelOption[] = [
  { id: 'gemini-3-pro-preview', display_name: 'gemini-3-pro-preview' },
  { id: 'gemini-3-flash-preview', display_name: 'gemini-3-flash-preview' },
  { id: 'gemini-2.5-pro', display_name: 'gemini-2.5-pro' },
  { id: 'gemini-2.5-flash', display_name: 'gemini-2.5-flash' },
  { id: 'gemini-2.5-flash-lite', display_name: 'gemini-2.5-flash-lite' },
]

export const LOCAL_CLI_MODEL_OPTIONS: Record<string, ModelOption[]> = {
  claude_cli: [{ id: 'claude-code', display_name: 'claude-code' }],
  codex_cli: [{ id: 'codex-cli', display_name: 'codex-cli' }],
  opencode_cli: [{ id: 'opencode-cli', display_name: 'opencode-cli' }],
  mimocode_cli: [{ id: 'xiaomi/mimo-v2.5-pro', display_name: 'xiaomi/mimo-v2.5-pro' }],
  cursor_cli: [{ id: 'cursor-agent', display_name: 'cursor-agent' }],
  kilocode_cli: [{ id: 'kilocode-cli', display_name: 'kilocode-cli' }],
  qwen_code_cli: [{ id: 'qwen-code-cli', display_name: 'qwen-code-cli' }],
  hermes_cli: [{ id: 'hermes-agent', display_name: 'hermes-agent' }],
  openclaw_cli: [{ id: 'openclaw-agent', display_name: 'openclaw-agent' }],
  dsh_cli: [{ id: 'dsh-cli', display_name: '跟随 DSH headless 当前模型' }],
  custom_cli: [{ id: 'custom-cli', display_name: 'custom-cli' }],
}

export const DEFAULT_CLI_COMMANDS: Record<string, string> = {
  claude_cli: 'claude',
  codex_cli: 'codex',
  opencode_cli: 'opencode',
  mimocode_cli: 'mimo',
  cursor_cli: 'agent',
  kilocode_cli: 'kilo',
  qwen_code_cli: 'qwen',
  hermes_cli: 'hermes',
  openclaw_cli: 'openclaw',
  dsh_cli: 'dsh',
  custom_cli: '',
}

export const DEFAULT_CLI_ARGS: Record<string, string> = {
  claude_cli: '["-p","{prompt}"]',
  codex_cli: '["exec","{prompt}"]',
  opencode_cli: '["run","--pure","--format","json","--model","{model}","{prompt}"]',
  mimocode_cli: '["run","{prompt}"]',
  cursor_cli: '["-p","--output-format","text","{prompt}"]',
  kilocode_cli: '["run","{prompt}"]',
  qwen_code_cli: '["--output-format","text","{prompt}"]',
  hermes_cli: '["--oneshot","{prompt}"]',
  openclaw_cli: '["agent","--local","--json","--session-key","agent:siming:local-cli","--message","{prompt}"]',
  dsh_cli: '["--profile","headless","{prompt}"]',
  custom_cli: '["{prompt}"]',
}

export const READINESS_LABELS: Record<ReadinessStatus, string> = {
  detected: '已检测，待验证',
  unverified: '待验证',
  testing: '正在测试',
  ready: '可用',
  auth_required: '需要登录',
  quota_limited: '额度受限',
  unavailable: '暂不可用',
}

export const readinessColor = (status: ReadinessStatus) => {
  if (status === 'ready') return 'success'
  if (status === 'testing') return 'processing'
  if (status === 'auth_required' || status === 'quota_limited') return 'warning'
  if (status === 'unavailable') return 'error'
  return 'default'
}

const FALLBACK_OUTPUT_LIMIT = 16000
const MODEL_OUTPUT_LIMITS: Record<string, number> = {
  'deepseek:deepseek-v4-pro': 384000,
  'deepseek:deepseek-v4-flash': 384000,
  'gemini:gemini-3-pro-preview': 65536,
  'gemini:gemini-3-flash-preview': 65536,
  'gemini:gemini-2.5-pro': 65536,
  'gemini:gemini-2.5-flash': 65536,
  'gemini:gemini-2.5-flash-lite': 65536,
}
const PROVIDER_OUTPUT_LIMITS: Record<string, number> = {
  deepseek: 384000,
  gemini: 65536,
}

export const fallbackModelOptions = (provider?: string): ModelOption[] => {
  if (provider === 'deepseek') return DEEPSEEK_MODEL_OPTIONS
  if (provider === 'gemini') return GEMINI_MODEL_OPTIONS
  if (provider && LOCAL_CLI_MODEL_OPTIONS[provider]) return LOCAL_CLI_MODEL_OPTIONS[provider]
  return []
}

export const normalizeDefaultModel = (provider: string, model: string) => {
  if (provider === 'deepseek' && model === 'deepseek-v3') return 'deepseek-v4-flash'
  if (provider === 'gemini' && model.startsWith('models/')) {
    return model.slice('models/'.length)
  }
  if (isLocalCliProvider(provider)) {
    return model || LOCAL_CLI_MODEL_OPTIONS[provider]?.[0]?.id || `${provider}-default`
  }
  return model
}

export const isDeepSeekModelSupported = (model: string) => (
  DEEPSEEK_MODEL_OPTIONS.some((option) => option.id === model)
)

export const normalizeProviderModelOptions = (provider: string, options: ModelOption[]) => {
  if (provider === 'gemini') {
    const normalized = options.map((option) => {
      const id = normalizeDefaultModel(provider, option.id)
      return {
        id,
        display_name: normalizeDefaultModel(provider, option.display_name || id),
      }
    })
    const unique = Array.from(new Map(normalized.map((option) => [option.id, option])).values())
    return unique.length > 0 ? unique : GEMINI_MODEL_OPTIONS
  }
  if (provider !== 'deepseek') return options
  const normalized = options
    .map((option) => ({
      id: normalizeDefaultModel(provider, option.id),
      display_name: normalizeDefaultModel(provider, option.display_name || option.id),
    }))
    .filter((option) => isDeepSeekModelSupported(option.id))
  const unique = Array.from(new Map(normalized.map((option) => [option.id, option])).values())
  return unique.length > 0 ? unique : DEEPSEEK_MODEL_OPTIONS
}

export const defaultOutputLimit = (provider?: string, model?: string) => {
  if (!provider) return FALLBACK_OUTPUT_LIMIT
  const key = `${provider}:${model || ''}`
  return MODEL_OUTPUT_LIMITS[key] || PROVIDER_OUTPUT_LIMITS[provider] || FALLBACK_OUTPUT_LIMIT
}

export const defaultSafetyLimits = (provider?: string, model?: string) => {
  const limit = defaultOutputLimit(provider, model)
  return {
    max_output_tokens: limit,
    deconstruct_input_char_limit: limit,
    deconstruct_item_char_limit: limit,
  }
}
