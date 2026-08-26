import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { createPortal } from 'react-dom'
import { useLocation, useNavigate } from 'react-router-dom'
import { Badge, Button, Drawer, Empty, Flex, Popconfirm, Progress, Space, Spin, Tag, Tooltip, Typography, message } from 'antd'
import { CheckOutlined, CloseCircleOutlined, ClockCircleOutlined, DeleteOutlined, PauseOutlined, PlayCircleOutlined, ReloadOutlined, UnorderedListOutlined } from '@ant-design/icons'
import { PersistentOutcome } from '../../../components/interaction'
import {
  operationKeys,
  markOperationAttentionRead,
  toInteractionProjection,
  updateOperationInCache,
  useDeleteOperations,
  useOperationAction,
  useOperations,
} from '..'
import type { OperationRun } from '..'
import { RuntimeStatusTags } from '../../../shared/ui/runtime'
import { apiDateTimeMs, parseApiDateTime } from '../../../utils/dateTime'

const { Paragraph, Text, Title } = Typography

const NON_TERMINAL_STATUSES = new Set<OperationRun['status']>(['queued', 'running', 'waiting_user', 'paused'])
const COMPUTING_STATUSES = new Set<OperationRun['status']>(['queued', 'running'])
const TERMINAL_STATUSES = new Set<OperationRun['status']>(['completed', 'failed', 'cancelled', 'interrupted'])

export interface OperationAttemptGroup {
  latest: OperationRun
  history: OperationRun[]
}

function operationTime(operation: OperationRun) {
  const value = operation.updated_at || operation.created_at
  const timestamp = apiDateTimeMs(value)
  return Number.isFinite(timestamp) ? timestamp : 0
}

function operationGroupKey(operation: OperationRun) {
  if (!operation.source_id) return `operation:${operation.id}`
  return [operation.project_id || 'global', operation.source_kind, operation.source_id].join(':')
}

export function groupOperationAttempts(operations: OperationRun[]): OperationAttemptGroup[] {
  const groups = new Map<string, OperationRun[]>()
  operations.forEach((operation) => {
    const key = operationGroupKey(operation)
    groups.set(key, [...(groups.get(key) || []), operation])
  })
  return Array.from(groups.values())
    .map((items) => {
      const sorted = [...items].sort((left, right) => operationTime(right) - operationTime(left))
      return { latest: sorted[0], history: sorted.slice(1) }
    })
    .sort((left, right) => operationTime(right.latest) - operationTime(left.latest))
}

export function operationNeedsAttention(operation: OperationRun) {
  return operation.status === 'waiting_user'
    || operation.health_status === 'stalled'
    || operation.health_status === 'disconnected'
    || (operation.status === 'failed' && operation.can_retry)
}
function elapsedLabel(seconds = 0) {
  const hours = Math.floor(seconds / 3600)
  const minutes = Math.floor((seconds % 3600) / 60)
  const secs = seconds % 60
  if (hours) return `${hours} 小时 ${minutes} 分`
  if (minutes) return `${minutes} 分 ${secs} 秒`
  return `${secs} 秒`
}

export function activityTimestamp(value?: string) {
  if (!value) return '尚无活动记录'
  const date = parseApiDateTime(value)
  if (!date) return '时间未知'
  const pad = (part: number) => String(part).padStart(2, '0')
  return [
    `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`,
    `${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`,
  ].join(' ')
}

function hoursSince(value?: string | null) {
  if (!value) return 0
  const timestamp = apiDateTimeMs(value)
  if (!Number.isFinite(timestamp)) return 0
  return Math.max(0, Math.floor((Date.now() - timestamp) / 3_600_000))
}

interface ModelOutputMetrics {
  kind: 'model_output'
  output_chars?: number
  output_preview?: string
  max_output_tokens?: number
  attempt?: number
}

function modelOutputMetrics(operation: OperationRun): ModelOutputMetrics | null {
  const metrics = operation.process_metrics as ModelOutputMetrics | null | undefined
  return metrics?.kind === 'model_output' ? metrics : null
}

function OperationItem({ operation, history, onAction, onDelete, onOpen, deletePending }: {
  operation: OperationRun
  history?: OperationRun[]
  onAction: (operation: OperationRun, action: string) => Promise<void>
  onDelete: (operations: OperationRun[]) => Promise<void>
  onOpen: (operation: OperationRun) => void
  deletePending?: boolean
}) {
  const active = NON_TERMINAL_STATUSES.has(operation.status)
  const canDelete = TERMINAL_STATUSES.has(operation.status)
  const computing = COMPUTING_STATUSES.has(operation.status)
  const progress = operation.progress || { mode: 'indeterminate' }
  const streamMetrics = modelOutputMetrics(operation)
  const interaction = toInteractionProjection(operation)
  const waitingHours = operation.status === 'waiting_user'
    ? hoursSince(operation.last_activity_at || operation.updated_at || operation.created_at)
    : 0
  return (
    <section className="operation-center-item" aria-label={operation.title}>
      <Flex justify="space-between" align="flex-start" gap={12}>
        <div className="operation-center-main">
          <Space size={6} wrap>
            <Text strong>{operation.title}</Text>
            <RuntimeStatusTags operation={operation} />
          </Space>
          <Paragraph className="operation-center-message" ellipsis={{ rows: 2, expandable: true, symbol: '展开' }}>
            {operation.current_message || '正在等待新的运行信息'}
          </Paragraph>
          {streamMetrics?.output_chars ? (
            <div className="operation-center-stream-output" aria-live="polite">
              <Space size={6} wrap>
                <Tag color="cyan">已输出 {streamMetrics.output_chars.toLocaleString()} 字</Tag>
                {streamMetrics.attempt ? <Tag>连续生成第 {streamMetrics.attempt} 段</Tag> : null}
                {streamMetrics.max_output_tokens ? <Tag>预算 {streamMetrics.max_output_tokens.toLocaleString()} tokens</Tag> : null}
              </Space>
              {streamMetrics.output_preview ? <Text>{streamMetrics.output_preview}</Text> : null}
            </div>
          ) : null}
        </div>
        <Space size={4}>
          {operation.status !== 'waiting_user' && (operation.attention?.action_url || operation.resume_url) && (
            <Button size="small" onClick={() => onOpen(operation)}>
              {operation.attention?.action_label || '查看'}
            </Button>
          )}
          {canDelete && (
            <Popconfirm
              title={`删除任务“${operation.title}”？`}
              description={history?.length
                ? `会删除当前记录及 ${history.length} 次历史尝试，且无法撤销。`
                : '任务记录删除后无法撤销。'}
              okText="删除"
              cancelText="保留"
              okButtonProps={{ danger: true }}
              onConfirm={() => onDelete([operation, ...(history || [])])}
            >
              <Button
                size="small"
                type="text"
                danger
                icon={<DeleteOutlined />}
                loading={deletePending}
                aria-label={`删除任务记录：${operation.title}`}
              >
                删除
              </Button>
            </Popconfirm>
          )}
        </Space>
      </Flex>

      {computing && progress.mode === 'determinate' && Boolean(progress.total) ? (
        <Progress
          percent={progress.percent || 0}
          size="small"
          format={() => `${progress.current || 0}/${progress.total}`}
          aria-label={`已完成 ${progress.current || 0}，共 ${progress.total}`}
        />
      ) : computing ? (
        <div className="operation-center-indeterminate" aria-live="polite">
          <Spin size="small" />
          <Text type="secondary">正在等待下一条真实活动，不估算完成百分比</Text>
        </div>
      ) : null}

      <div className="operation-center-facts">
        <span><ClockCircleOutlined /> 已运行 {elapsedLabel(operation.elapsed_seconds)}</span>
        <span>最近活动 {activityTimestamp(operation.last_activity_at || undefined)}</span>
      </div>
      {waitingHours >= 24 && (
        <Text className="operation-center-stale" type="warning">
          已等待确认 {waitingHours} 小时，请处理后再继续流程。
        </Text>
      )}
      {interaction.outcome && (operation.status === 'waiting_user' || !active) && (
        <PersistentOutcome
          className="operation-center-outcome"
          outcome={interaction.outcome}
          attention={interaction.attention}
          result={interaction.result || { summary: operation.result_summary || undefined }}
          onAction={operation.attention?.action_url || operation.resume_url ? () => onOpen(operation) : undefined}
        />
      )}
      {operation.next_action && <Text className="operation-center-next" type="secondary">下一步：{operation.next_action}</Text>}
      {active && (
        <Space size={6} wrap className="operation-center-actions">
          {operation.can_pause && operation.status !== 'paused' && operation.status !== 'waiting_user' && <Button size="small" icon={<PauseOutlined />} onClick={() => void onAction(operation, 'pause')}>暂停</Button>}
          {operation.can_pause && operation.status === 'paused' && <Button size="small" type="primary" icon={<PlayCircleOutlined />} onClick={() => void onAction(operation, 'continue')}>继续</Button>}
          {operation.can_retry && operation.health_status !== 'active' && <Button size="small" icon={<ReloadOutlined />} onClick={() => void onAction(operation, 'retry-current-unit')}>重试当前单元</Button>}
          {operation.can_cancel && operation.status !== 'waiting_user' && <Button size="small" danger icon={<CloseCircleOutlined />} onClick={() => void onAction(operation, 'cancel')}>取消</Button>}
        </Space>
      )}
      {(operation.phase || operation.model_source || operation.source_kind) && (
        <details className="operation-center-details">
          <summary>技术详情</summary>
          <div>
            {operation.phase && <span>阶段：{operation.phase}</span>}
            {operation.model_source && <span>模型：{operation.model_source}</span>}
            <span>来源：{operation.source_kind}</span>
          </div>
        </details>
      )}
      {history && history.length > 0 && (
        <details className="operation-center-history">
          <summary>历史尝试 {history.length}</summary>
          <div className="operation-center-history-list">
            {history.map((attempt) => (
              <div key={attempt.id}>
                <RuntimeStatusTags operation={attempt} />
                <Text type="secondary">{activityTimestamp(attempt.updated_at || attempt.created_at || undefined)}</Text>
              </div>
            ))}
          </div>
        </details>
      )}
    </section>
  )
}

export default function GlobalOperationCenter() {
  const navigate = useNavigate()
  const location = useLocation()
  const queryClient = useQueryClient()
  const {
    data: operationItems,
    isError: pollDisconnected,
    refetch: refetchOperations,
  } = useOperations(30)
  const {
    mutateAsync: runOperationAction,
    isPending: actionPending,
    variables: actionVariables,
  } = useOperationAction(30)
  const {
    mutateAsync: deleteOperations,
    isPending: deletePending,
    variables: deletingOperationIds,
  } = useDeleteOperations(30)
  const [open, setOpen] = useState(false)
  const [navTarget, setNavTarget] = useState<HTMLElement | null>(null)
  const [streamDisconnected, setStreamDisconnected] = useState(false)
  const [locallyReadOperationIds, setLocallyReadOperationIds] = useState<Set<string>>(() => new Set())
  const streamRef = useRef<EventSource | null>(null)
  const operations = useMemo(() => operationItems || [], [operationItems])
  const actionId = actionPending ? actionVariables?.operationId : undefined

  useEffect(() => {
    const attach = () => {
      const target = document.getElementById('global-operation-nav-slot')
      setNavTarget(target)
      return Boolean(target)
    }
    if (attach()) return
    const observer = new MutationObserver(() => {
      if (attach()) observer.disconnect()
    })
    observer.observe(document.body, { childList: true, subtree: true })
    return () => observer.disconnect()
  }, [location.pathname])

  const groupedOperations = useMemo(() => groupOperationAttempts(operations), [operations])
  const attentionOperations = useMemo(
    () => groupedOperations.filter((group) => operationNeedsAttention(group.latest)),
    [groupedOperations],
  )
  const unreadAttentionOperations = useMemo(
    () => attentionOperations.filter((group) => !group.latest.attention_read_at && !locallyReadOperationIds.has(group.latest.id)),
    [attentionOperations, locallyReadOperationIds],
  )
  const runningOperations = useMemo(
    () => groupedOperations.filter((group) => COMPUTING_STATUSES.has(group.latest.status) && !operationNeedsAttention(group.latest)),
    [groupedOperations],
  )
  const pausedOperations = useMemo(
    () => groupedOperations.filter((group) => group.latest.status === 'paused' && !operationNeedsAttention(group.latest)),
    [groupedOperations],
  )
  const recentOperations = useMemo(
    () => groupedOperations
      .filter((group) => !NON_TERMINAL_STATUSES.has(group.latest.status) && !operationNeedsAttention(group.latest))
      .slice(0, 10),
    [groupedOperations],
  )
  const primaryActiveId = runningOperations[0]?.latest.id

  useEffect(() => {
    streamRef.current?.close()
    streamRef.current = null
    if (!open || !primaryActiveId) return
    const source = new EventSource(`/api/v1/operations/${primaryActiveId}/stream`)
    streamRef.current = source
    source.onopen = () => setStreamDisconnected(false)
    source.addEventListener('heartbeat', (event) => {
        setStreamDisconnected(false)
      try {
        const next = JSON.parse((event as MessageEvent).data) as OperationRun
        queryClient.setQueryData<OperationRun[]>(
          operationKeys.list(30),
          (current) => updateOperationInCache(current, next),
        )
      } catch { /* polling remains authoritative */ }
    })
    source.addEventListener('done', () => {
      source.close()
      setStreamDisconnected(false)
      void refetchOperations()
    })
    source.onerror = () => setStreamDisconnected(true)
    return () => source.close()
  }, [open, primaryActiveId, queryClient, refetchOperations])

  const runAction = useCallback(async (operation: OperationRun, action: string) => {
    try {
      await runOperationAction({ operationId: operation.id, action })
    } catch (error) {
      message.error(error instanceof Error ? error.message : '任务操作失败')
    }
  }, [runOperationAction])

  const deleteFinishedOperations = useCallback(async (items: OperationRun[]) => {
    const ids = items.map((item) => item.id)
    try {
      await deleteOperations(ids)
      message.success(items.length > 1 ? `已删除任务及 ${items.length - 1} 次历史尝试` : '任务记录已删除')
    } catch (error) {
      message.error(error instanceof Error ? error.message : '删除任务记录失败')
    }
  }, [deleteOperations])

  const openResult = useCallback((operation: OperationRun) => {
    const target = operation.attention?.action_url || operation.resume_url
    if (target) navigate(target)
    setOpen(false)
  }, [navigate])

  const markAllAttentionRead = useCallback(async () => {
    const ids = unreadAttentionOperations.map((group) => group.latest.id)
    if (!ids.length) return
    const readAt = new Date().toISOString()
    setLocallyReadOperationIds((current) => new Set([...current, ...ids]))
    queryClient.setQueryData<OperationRun[]>(operationKeys.list(30), (current) => (
      current?.map((operation) => ids.includes(operation.id)
        ? { ...operation, attention_read_at: readAt }
        : operation)
    ))
    try {
      await markOperationAttentionRead(ids)
      await refetchOperations()
    } catch (error) {
      await refetchOperations()
      message.error(error instanceof Error ? error.message : '标记已读失败')
    }
  }, [queryClient, refetchOperations, unreadAttentionOperations])

  const trigger = (
    <Tooltip title="查看待处理、运行中和最近任务">
      <Badge
        count={unreadAttentionOperations.length}
        size="small"
        className={`global-operation-badge${navTarget ? '' : ' global-operation-badge-floating'}`}
      >
        <Button
          className={`global-operation-trigger${runningOperations.length ? ' global-operation-trigger-running' : ''}`}
          icon={<UnorderedListOutlined />}
          aria-label={`全局任务中心，${unreadAttentionOperations.length} 项未读提醒，${attentionOperations.length} 项待处理，${runningOperations.length} 项运行中`}
          onClick={() => setOpen(true)}
        >
          任务
        </Button>
      </Badge>
    </Tooltip>
  )

  const renderGroups = (groups: OperationAttemptGroup[]) => groups.map((group) => (
    <div key={group.latest.id} aria-busy={actionId === group.latest.id || Boolean(deletePending && deletingOperationIds?.includes(group.latest.id))}>
      <OperationItem
        operation={group.latest}
        history={group.history}
        onAction={runAction}
        onDelete={deleteFinishedOperations}
        onOpen={openResult}
        deletePending={Boolean(deletePending && deletingOperationIds?.includes(group.latest.id))}
      />
    </div>
  ))

  return (
    <>
      {navTarget ? createPortal(trigger, navTarget) : trigger}
      <Drawer title={<Space><UnorderedListOutlined /><span>任务中心</span></Space>} open={open} onClose={() => setOpen(false)} width={520} className="operation-center-drawer">
        {(pollDisconnected || streamDisconnected) && (
          <div className="operation-center-reconnecting" role="status" aria-live="polite">
            <Spin size="small" />
            <Text>{pollDisconnected ? '正在重新连接司命，后台任务不会因此停止' : '进度流正在重新连接，已改用状态轮询'}</Text>
          </div>
        )}
        {groupedOperations.length === 0 && <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="尚无任务记录" />}
        {attentionOperations.length > 0 && (
          <section className="operation-center-section" aria-labelledby="operation-attention-title">
            <Flex justify="space-between" align="center" gap={12}>
              <Title level={5} id="operation-attention-title">
                待你处理 <Badge count={unreadAttentionOperations.length} aria-hidden={unreadAttentionOperations.length === 0} />
              </Title>
              <Button
                size="small"
                type="text"
                icon={<CheckOutlined />}
                aria-label="全部标为已读"
                disabled={unreadAttentionOperations.length === 0}
                onClick={() => void markAllAttentionRead()}
              >
                全部标为已读
              </Button>
            </Flex>
            {renderGroups(attentionOperations)}
          </section>
        )}
        {runningOperations.length > 0 && (
          <section className="operation-center-section" aria-labelledby="operation-running-title">
            <Title level={5} id="operation-running-title">正在运行 <Badge status="processing" /></Title>
            {renderGroups(runningOperations)}
          </section>
        )}
        {pausedOperations.length > 0 && (
          <section className="operation-center-section" aria-labelledby="operation-paused-title">
            <Title level={5} id="operation-paused-title">已暂停</Title>
            {renderGroups(pausedOperations)}
          </section>
        )}
        {recentOperations.length > 0 && (
          <section className="operation-center-section operation-center-recent" aria-labelledby="operation-recent-title">
            <Title level={5} id="operation-recent-title">最近完成</Title>
            {renderGroups(recentOperations)}
          </section>
        )}
      </Drawer>
    </>
  )
}
