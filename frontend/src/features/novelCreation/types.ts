export interface ApiResponse<T> {
  code: number
  message: string
  data: T
}

export interface PresetDefaults {
  world_tone: string
  story_structure: string
  pacing: string
  writing_style: string
  special_requirements: string[]
  avoid: string[]
}

export interface GenrePreset {
  id: string
  label: string
  description: string
  themes: Array<{ id: string; label: string }>
  defaults: PresetDefaults
}

export interface PresetCatalog {
  categories: GenrePreset[]
  platforms: string[]
  audiences: string[]
  length_options: Array<{ id: string; label: string; words: number; chapters: number }>
  stage_order: string[]
  stage_labels: Record<string, string>
}

export interface ConceptCard {
  id: string
  title: string
  subtitle?: string
  logline: string
  source_index: number
  protagonist_seed: { name: string; identity: string; goal: string; lack: string }
  world_hook: string
  core_conflict: string
  story_engine: string
  opening_hook: string
  differentiators: string[]
  risks: string[]
  coverage: { score: number; covered: string[]; missing: string[] }
}

export interface StageState {
  status: 'pending' | 'generated' | 'confirmed' | 'stale' | 'conflict'
  data?: Record<string, unknown> | null
  source?: string
  stale_reason?: string
  updated_at?: string
}

export interface StageFlowItem {
  stage: string
  label: string
  status: StageState['status']
  can_confirm: boolean
  actions: string[]
  next_stage?: string | null
}

export interface StageFlow {
  attention_stage?: string | null
  recommended_stage?: string | null
  legacy_current_stage?: string | null
  pending_confirmations: string[]
  items: Record<string, StageFlowItem>
}

export interface CreationFormValues {
  brief: string
  preset_id: string
  theme_id?: string
  genre: string
  target_audience: string
  platform: string
  target_words: number
  target_chapters: number
  world_tone: string
  story_structure: string
  pacing: string
  writing_style: string
  special_requirements: string[]
  avoid: string[]
  author_brief?: string
  author_outline?: string
  locked_requirements?: string[]
}

export interface CreationSession {
  id: string
  status: string
  current_stage?: string
  created_project_id?: string
  revision: number
  stage_flow?: StageFlow
  updated_at?: string
  last_error?: {
    failure_class?: string
    message?: string
    next_action?: string
    run_id?: string
    failed_stage?: string
    failed_stage_label?: string
  }
  runs?: StageRun[]
  draft?: {
    schema_version?: number
    creation_mode?: 'author_led' | 'explore'
    author_brief?: string
    author_outline?: string
    locked_requirements?: string[]
    form: CreationFormValues
    concepts: ConceptCard[]
    selected_concept_id?: string
    quick_mode?: boolean
    stages: Record<string, StageState>
  }
}

export interface StageRun {
  id: string
  session_id?: string
  stage: string
  status: string
  current_message?: string
  failure_class?: string
  next_action?: string
  operation_id?: string
  input_revision?: number
  input_snapshot_hash?: string
  model_source?: string
  attempt?: number
  result_mode?: 'model' | 'repaired'
  warning?: string
  diagnostic_count?: number
  stream_progress?: {
    kind: 'model_output'
    output_chars: number
    output_preview?: string
    max_output_tokens?: number
    attempt?: number
  }
  result?: {
    attempt?: number
    result_mode?: 'model' | 'repaired'
    warning?: string
  }
  created_at?: string
  updated_at?: string
  completed_at?: string
  events?: Array<{
    event_type: string
    status?: string
    message?: string
    payload?: Record<string, unknown>
  }>
}

export const CORE_STAGES = ['world_style', 'characters', 'locations', 'macro_outline', 'opening_outline', 'final_review']
export const ACTIVE_RUN_STATUSES = new Set(['queued', 'running'])
export const TERMINAL_RUN_STATUSES = new Set(['completed', 'waiting_user', 'waiting_author', 'failed', 'cancelled', 'interrupted', 'superseded'])
export type CreationPath = 'author_led' | 'explore'

export function runAttempt(run: StageRun) {
  return run.attempt ?? run.result?.attempt ?? null
}

export function runResultModeLabel(run: StageRun) {
  const mode = run.result_mode || run.result?.result_mode
  if (mode === 'repaired') return '同模型结构修复'
  if (mode === 'model') return '模型直接生成'
  return '未记录'
}

export function runSaveResult(run: StageRun) {
  if (run.status === 'completed') return '阶段内容已由作者确认'
  if (run.status === 'waiting_user' || run.status === 'waiting_author') return '阶段结果已保存，等待作者确认'
  if (run.status === 'paused') return '任务已暂停，检查点和已有草稿均已保留'
  if (run.status === 'cancelled') return '已保留取消前保存的内容，未覆盖原草稿'
  if (run.status === 'interrupted') return '任务在应用关闭或服务重启时中断，可按原输入重新生成'
  return '失败结果未写入，原草稿保持不变'
}

export function errorText(error: unknown) {
  return error instanceof Error ? error.message : '操作失败，请稍后重试'
}

export function splitLines(value: string) {
  return value.split(/\r?\n/).map((item) => item.trim()).filter(Boolean)
}

export function stageTone(status?: StageState['status']) {
  if (status === 'confirmed') return 'success'
  if (status === 'stale') return 'warning'
  if (status === 'conflict') return 'error'
  if (status === 'generated') return 'processing'
  return 'default'
}

export function stageStatusLabel(status?: StageState['status']) {
  const labels: Record<string, string> = {
    pending: '待生成',
    generated: '待确认',
    confirmed: '已确认',
    stale: '需重新校验',
    conflict: '版本冲突',
  }
  return labels[status || 'pending'] || '待生成'
}
