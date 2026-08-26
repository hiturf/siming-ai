import { useCallback, useEffect, useRef, useState, type Dispatch, type MutableRefObject, type SetStateAction } from 'react'
import { message } from 'antd'
import { apiClient } from '../../api/client'
import {
  ACTIVE_RUN_STATUSES,
  CORE_STAGES,
  TERMINAL_RUN_STATUSES,
  type ApiResponse,
  type CreationSession,
  type StageRun,
} from './types'

type ConnectionState = 'connected' | 'reconnecting'

interface UseNovelCreationRunOptions {
  session: CreationSession | null
  requestedRunId: string | null
  loadSession: (sessionId: string) => Promise<CreationSession>
  invalidateSessionLoads: () => void
  focusStageHeading: () => void
  editedDuringRunRef: MutableRefObject<boolean>
  setBusy: Dispatch<SetStateAction<boolean>>
  setRunMessage: Dispatch<SetStateAction<string>>
  setRunProgress: Dispatch<SetStateAction<number>>
  setRunConnection: Dispatch<SetStateAction<ConnectionState>>
  setResultRevisionNotice: Dispatch<SetStateAction<string>>
}

interface ActiveWatch {
  generation: number
  runId: string
  ownerSessionId?: string
  source: EventSource
}

export function useNovelCreationRun({
  session,
  requestedRunId,
  loadSession,
  invalidateSessionLoads,
  focusStageHeading,
  editedDuringRunRef,
  setBusy,
  setRunMessage,
  setRunProgress,
  setRunConnection,
  setResultRevisionNotice,
}: UseNovelCreationRunOptions) {
  const [activeRunState, setActiveRunState] = useState<StageRun | null>(null)
  const [cancellingRun, setCancellingRun] = useState(false)
  const [pausingRun, setPausingRun] = useState(false)
  const activeRunRef = useRef<StageRun | null>(null)
  const watchRef = useRef<ActiveWatch | null>(null)
  const generationRef = useRef(0)
  const cancelInFlightRef = useRef(false)

  const setActiveRun = useCallback((value: SetStateAction<StageRun | null>) => {
    setActiveRunState((previous) => {
      const next = typeof value === 'function'
        ? (value as (current: StageRun | null) => StageRun | null)(previous)
        : value
      activeRunRef.current = next
      return next
    })
  }, [])

  const isCurrentWatch = useCallback((generation: number, runId: string) => (
    generationRef.current === generation && watchRef.current?.runId === runId
  ), [])

  const disposeRunWatch = useCallback(() => {
    generationRef.current += 1
    watchRef.current?.source.close()
    watchRef.current = null
    cancelInFlightRef.current = false
    setCancellingRun(false)
    setPausingRun(false)
  }, [])

  const clearRunState = useCallback(() => {
    disposeRunWatch()
    setActiveRun(null)
    setBusy(false)
    setRunMessage('')
    setRunProgress(0)
    setRunConnection('connected')
    setResultRevisionNotice('')
  }, [disposeRunWatch, setActiveRun, setBusy, setResultRevisionNotice, setRunConnection, setRunMessage, setRunProgress])

  useEffect(() => disposeRunWatch, [disposeRunWatch])

  const refreshOwnedSession = useCallback(async (sessionId: string | undefined, generation: number) => {
    if (!sessionId || generationRef.current !== generation) return
    try {
      await loadSession(sessionId)
      if (generationRef.current === generation) focusStageHeading()
    } catch {
      // The durable run remains authoritative; a later reconnect can refresh it.
    }
  }, [focusStageHeading, loadSession])

  const finishRun = useCallback((run: StageRun, generation?: number) => {
    const watch = watchRef.current
    const effectiveGeneration = generation ?? generationRef.current
    if (generation != null && !isCurrentWatch(generation, run.id)) return
    if (watch?.generation === effectiveGeneration && watch.runId === run.id) {
      watch.source.close()
      watchRef.current = null
    }
    setActiveRun(run)
    cancelInFlightRef.current = false
    setCancellingRun(false)
    setRunConnection('connected')
    setBusy(false)
    setRunMessage('')
    setRunProgress(run.status === 'completed' ? 100 : 0)

    if (run.status === 'failed') message.error(run.current_message || '阶段生成失败')
    else if (run.status === 'cancelled') message.info('立项任务已取消，已保存草稿保持不变')
    else message.success('阶段结果已保存到立项草稿')

    if (run.input_revision != null) {
      const suffix = editedDuringRunRef.current ? '；运行期间的新修改已保存为下一版，不会被旧结果覆盖' : ''
      const repairedStages = (run.events || [])
        .filter((item) => item.event_type === 'stage_repaired')
        .map((item) => String(item.payload?.stage || '未知阶段'))
      const repairNotice = repairedStages.length > 0
        ? `；${repairedStages.length} 个阶段的模型回复不可用，已采用安全结构并保留供你审阅`
        : ''
      setResultRevisionNotice(`本次结果基于草稿 v${run.input_revision}${suffix}${repairNotice}`)
    }

    const ownerSessionId = run.session_id || watch?.ownerSessionId
    void refreshOwnedSession(ownerSessionId, effectiveGeneration)
  }, [editedDuringRunRef, isCurrentWatch, refreshOwnedSession, setActiveRun, setBusy, setResultRevisionNotice, setRunConnection, setRunMessage, setRunProgress])

  const watchRun = useCallback((runId: string, ownerSessionId?: string) => {
    if (watchRef.current?.runId === runId) return
    disposeRunWatch()
    invalidateSessionLoads()
    const generation = ++generationRef.current
    setBusy(true)
    setRunConnection('connected')
    const source = new EventSource(`/api/v1/novel-creation/runs/${runId}/stream`)
    watchRef.current = { generation, runId, ownerSessionId, source }

    const current = () => isCurrentWatch(generation, runId)
    source.onopen = () => {
      if (current()) setRunConnection('connected')
    }
    const handleEvent = (event: MessageEvent) => {
      if (!current()) return
      try {
        const payload = JSON.parse(event.data) as {
          message?: string
          event_type?: string
          payload?: {
            stage?: string
            kind?: 'model_output'
            output_chars?: number
            output_preview?: string
            max_output_tokens?: number
            attempt?: number
          }
        }
        if (payload.message) setRunMessage(payload.message)
        if (payload.event_type === 'model_output' && payload.payload?.kind === 'model_output') {
          setActiveRun((previous) => previous && previous.id === runId
            ? {
                ...previous,
                stream_progress: {
                  kind: 'model_output',
                  output_chars: Number(payload.payload?.output_chars || 0),
                  output_preview: payload.payload?.output_preview,
                  max_output_tokens: payload.payload?.max_output_tokens,
                  attempt: payload.payload?.attempt,
                },
              }
            : previous)
        }
        const stageIndex = payload.payload?.stage ? CORE_STAGES.indexOf(payload.payload.stage) : -1
        if (stageIndex >= 0) {
          const completed = payload.event_type === 'stage_completed'
          setRunProgress(Math.round(((stageIndex + (completed ? 1 : 0.25)) / CORE_STAGES.length) * 100))
        }
        if (payload.event_type === 'stage_completed') {
          void refreshOwnedSession(ownerSessionId, generation)
        }
      } catch {
        // Keep the last readable status.
      }
    }
    for (const eventName of ['started', 'model_output', 'stage_progress', 'stage_repaired', 'stage_completed', 'completed', 'failed']) {
      source.addEventListener(eventName, handleEvent as EventListener)
    }
    source.addEventListener('done', (event) => {
      if (!current()) return
      try {
        finishRun(JSON.parse((event as MessageEvent).data) as StageRun, generation)
      } catch {
        setRunMessage('任务已结束，正在读取最终状态...')
        void apiClient.get<ApiResponse<StageRun>>(`/novel-creation/runs/${runId}`)
          .then((response) => {
            if (current() && TERMINAL_RUN_STATUSES.has(response.data.data.status)) finishRun(response.data.data, generation)
          })
      }
    })
    source.onerror = () => {
      if (!current()) return
      setRunConnection('reconnecting')
      setRunMessage('进度连接中断，正在重新连接；后台任务仍在运行...')
      void apiClient.get<ApiResponse<StageRun>>(`/novel-creation/runs/${runId}`)
        .then((response) => {
          if (!current()) return
          const run = response.data.data
          if (TERMINAL_RUN_STATUSES.has(run.status)) {
            finishRun(run, generation)
            return
          }
          setActiveRun(run)
          if (run.current_message) setRunMessage(run.current_message)
        })
        .catch(() => undefined)
    }
  }, [disposeRunWatch, finishRun, invalidateSessionLoads, isCurrentWatch, refreshOwnedSession, setActiveRun, setBusy, setRunConnection, setRunMessage, setRunProgress])

  useEffect(() => {
    if (!session?.id) return
    let disposed = false
    const runs = session.runs || []
    const newestFirst = [...runs].reverse()

    const restoreRun = (run: StageRun) => {
      if (disposed) return
      setActiveRun((previous) => previous?.id === run.id
        ? { ...previous, ...run, events: run.events || previous.events }
        : run)
      if (ACTIVE_RUN_STATUSES.has(run.status)) {
        setBusy(true)
        setRunProgress(0)
        setRunMessage(run.current_message || '正在恢复立项任务...')
        watchRun(run.id, run.session_id || session.id)
      } else if (TERMINAL_RUN_STATUSES.has(run.status)) {
        setBusy(false)
        setRunConnection('connected')
        setRunMessage('')
        setRunProgress(run.status === 'completed' ? 100 : 0)
      }
    }

    const localRun = activeRunRef.current
    const localRunBelongsToSession = localRun
      && (!localRun.session_id || localRun.session_id === session.id)
    const authoritativeLocalRun = localRunBelongsToSession
      && (ACTIVE_RUN_STATUSES.has(localRun.status)
        || localRun.status === 'paused'
        || (TERMINAL_RUN_STATUSES.has(localRun.status) && requestedRunId === localRun.id))
      ? localRun
      : undefined
    const requestedRun = requestedRunId ? runs.find((run) => run.id === requestedRunId) : undefined
    const fallbackRun = newestFirst.find((run) => ACTIVE_RUN_STATUSES.has(run.status))
      || newestFirst.find((run) => run.status === 'paused')
      || newestFirst.find((run) => TERMINAL_RUN_STATUSES.has(run.status))
    const localTarget = authoritativeLocalRun || requestedRun || (!requestedRunId ? fallbackRun : undefined)
    if (localTarget) {
      restoreRun(localTarget)
      return () => { disposed = true }
    }
    if (!requestedRunId) {
      setActiveRun(null)
      setBusy(false)
      return () => { disposed = true }
    }

    void apiClient.get<ApiResponse<StageRun>>(`/novel-creation/runs/${requestedRunId}`)
      .then((response) => {
        const run = response.data.data
        if (run.session_id && run.session_id !== session.id) {
          if (fallbackRun) restoreRun(fallbackRun)
          return
        }
        restoreRun(run)
      })
      .catch(() => {
        if (fallbackRun) restoreRun(fallbackRun)
      })
    return () => { disposed = true }
  }, [requestedRunId, session?.id, session?.runs, setActiveRun, setBusy, setRunConnection, setRunMessage, setRunProgress, watchRun])

  const cancelActiveRun = useCallback(async () => {
    const run = activeRunRef.current
    if (!run?.operation_id || cancelInFlightRef.current) return
    cancelInFlightRef.current = true
    setCancellingRun(true)
    try {
      await apiClient.post(`/operations/${run.operation_id}/cancel`)
      setRunMessage('正在取消任务；已保存的草稿不会丢失...')
      message.info('取消请求已发送')
    } catch (error) {
      cancelInFlightRef.current = false
      setCancellingRun(false)
      message.error(error instanceof Error ? error.message : '取消任务失败，请重试')
    }
  }, [setRunMessage])

  const pauseActiveRun = useCallback(async () => {
    const run = activeRunRef.current
    if (!run?.operation_id || run.status !== 'running' || pausingRun) return
    setPausingRun(true)
    try {
      await apiClient.post(`/operations/${run.operation_id}/pause`)
      setActiveRun((current) => current ? { ...current, status: 'paused', current_message: '任务已暂停；检查点和已有草稿均已保留' } : current)
      setBusy(false)
      setRunMessage('')
      message.info('任务已暂停')
    } catch (error) {
      message.error(error instanceof Error ? error.message : '暂停任务失败，请重试')
    } finally {
      setPausingRun(false)
    }
  }, [pausingRun, setActiveRun, setBusy, setRunMessage])

  const resumeActiveRun = useCallback(async () => {
    const run = activeRunRef.current
    if (!run?.operation_id || run.status !== 'paused' || pausingRun) return
    setPausingRun(true)
    try {
      await apiClient.post(`/operations/${run.operation_id}/continue`)
      setActiveRun((current) => current ? { ...current, status: 'running', current_message: '正在从最近检查点继续' } : current)
      setBusy(true)
      setRunMessage('正在从最近检查点继续')
      watchRun(run.id, run.session_id)
    } catch (error) {
      message.error(error instanceof Error ? error.message : '继续任务失败，请重试')
    } finally {
      setPausingRun(false)
    }
  }, [pausingRun, setActiveRun, setBusy, setRunMessage, watchRun])

  return {
    activeRun: activeRunState,
    setActiveRun,
    cancellingRun,
    pausingRun,
    cancelActiveRun,
    pauseActiveRun,
    resumeActiveRun,
    watchRun,
    disposeRunWatch,
    clearRunState,
  }
}
