import { Alert, Button, Descriptions, Progress, Space, Spin, Tag, Typography } from 'antd'
import { CloseCircleOutlined, CloudSyncOutlined, PauseCircleOutlined, PlayCircleOutlined } from '@ant-design/icons'
import { runAttempt, runResultModeLabel, runSaveResult, type StageRun } from './types'

const { Text } = Typography

interface RunStatusPanelsProps {
  busy: boolean
  activeRun: StageRun | null
  stageLabels: Record<string, string>
  runMessage: string
  runConnection: 'connected' | 'reconnecting'
  runProgress: number
  editedDuringRun: boolean
  cancellingRun: boolean
  pausingRun: boolean
  resultRevisionNotice: string
  onCancel: () => void
  onPause: () => void
  onResume: () => void
  onAcceptResult: () => void
  onRegenerateLatest: () => void
}

export function RunStatusPanels({
  busy,
  activeRun,
  stageLabels,
  runMessage,
  runConnection,
  runProgress,
  editedDuringRun,
  cancellingRun,
  pausingRun,
  resultRevisionNotice,
  onCancel,
  onPause,
  onResume,
  onAcceptResult,
  onRegenerateLatest,
}: RunStatusPanelsProps) {
  const streamProgress = activeRun?.stream_progress
  return (
    <>
      {busy && (
        <div className="creation-run-bar" aria-live="polite">
          <CloudSyncOutlined spin />
          <div className="creation-run-detail">
            <Space size={6} wrap>
              <Text strong>{runMessage || '正在处理立项任务...'}</Text>
              <Tag color={runConnection === 'connected' ? 'processing' : 'warning'}>{runConnection === 'connected' ? '运行中' : '正在重新连接'}</Tag>
              {activeRun && <Tag>阶段：{stageLabels[activeRun.stage] || activeRun.stage}</Tag>}
              {activeRun && <Tag>实际模型：{activeRun.model_source || '等待任务上报'}</Tag>}
              {activeRun && <Tag>尝试次数：{runAttempt(activeRun) ?? '等待任务上报'}</Tag>}
              {streamProgress?.output_chars ? <Tag color="cyan">已输出：{streamProgress.output_chars.toLocaleString()} 字</Tag> : null}
              {streamProgress?.max_output_tokens ? <Tag>输出预算：{streamProgress.max_output_tokens.toLocaleString()} tokens</Tag> : null}
              {activeRun?.input_revision != null && <Tag>基于草稿 v{activeRun.input_revision}</Tag>}
            </Space>
            {activeRun?.stage === 'all' && runProgress > 0
              ? <Progress percent={runProgress} status="active" showInfo />
              : <div className="creation-run-indeterminate"><Spin size="small" /><Text type="secondary">模型正在推进；无法准确估算时不显示虚假百分比</Text></div>}
            {streamProgress?.output_preview ? (
              <div className="creation-run-stream-preview" aria-live="polite">
                <Text type="secondary">实时输出</Text>
                <Text>{streamProgress.output_preview}</Text>
              </div>
            ) : null}
            {editedDuringRun && <Text type="warning">你刚才的修改会保存为下一版，不会改变当前这次生成。</Text>}
          </div>
          <Space wrap>
            <Button icon={<PauseCircleOutlined />} loading={pausingRun} disabled={!activeRun?.operation_id || pausingRun || cancellingRun} onClick={onPause}>
              暂停
            </Button>
            <Button danger icon={<CloseCircleOutlined />} loading={cancellingRun} disabled={!activeRun?.operation_id || cancellingRun || pausingRun} onClick={onCancel}>
              {cancellingRun ? '正在取消' : '取消任务'}
            </Button>
          </Space>
        </div>
      )}

      {!busy && activeRun && ['completed', 'waiting_user', 'waiting_author', 'paused', 'failed', 'cancelled', 'interrupted', 'superseded'].includes(activeRun.status) && (
        <Alert
          className="creation-run-outcome"
          type={activeRun.status === 'failed' ? 'error' : ['paused', 'cancelled', 'interrupted', 'superseded'].includes(activeRun.status) ? 'warning' : 'success'}
          showIcon
          message={['waiting_user', 'waiting_author'].includes(activeRun.status)
            ? '阶段结果等待作者确认'
            : activeRun.status === 'completed'
              ? '本轮立项任务已完成'
              : activeRun.status === 'paused'
                ? '本轮立项任务已暂停'
              : activeRun.status === 'cancelled'
                ? '本轮立项任务已取消'
                : activeRun.status === 'interrupted'
                  ? '本轮立项任务已中断'
                  : activeRun.status === 'superseded'
                    ? '本轮立项任务已被新版本取代'
                    : '本轮立项任务失败'}
          description={(
            <Descriptions className="creation-run-outcome-details" size="small" column={1} colon={false}>
              <Descriptions.Item label="状态说明">{activeRun.current_message || (['waiting_user', 'waiting_author'].includes(activeRun.status) ? '生成结果已保存，等待作者确认' : activeRun.status === 'paused' ? '任务已暂停，检查点和已有草稿均已保留' : activeRun.status === 'completed' ? '任务已完成' : '任务已结束')}</Descriptions.Item>
              <Descriptions.Item label="阶段">{stageLabels[activeRun.stage] || activeRun.stage}</Descriptions.Item>
              <Descriptions.Item label="实际模型">{activeRun.model_source || '未记录'}</Descriptions.Item>
              <Descriptions.Item label="尝试次数">{runAttempt(activeRun) == null ? '未记录' : `${runAttempt(activeRun)} 次`}</Descriptions.Item>
              <Descriptions.Item label="结果模式">{runResultModeLabel(activeRun)}</Descriptions.Item>
              <Descriptions.Item label="保存结果">{runSaveResult(activeRun)}</Descriptions.Item>
              <Descriptions.Item label="警告">{(activeRun.warning || activeRun.result?.warning) ? <Text type="warning">{activeRun.warning || activeRun.result?.warning}</Text> : '无'}</Descriptions.Item>
              <Descriptions.Item label="下一步">{activeRun.next_action || (['waiting_user', 'waiting_author'].includes(activeRun.status) ? '审阅并确认本阶段，或编辑后重新生成' : activeRun.status === 'paused' ? '继续任务，或取消本轮并保留已有草稿' : activeRun.status === 'completed' ? '继续处理下一阶段' : '检查当前草稿后可重新生成本阶段')}</Descriptions.Item>
            </Descriptions>
          )}
          action={activeRun.status === 'paused' ? <Button type="primary" icon={<PlayCircleOutlined />} loading={pausingRun} onClick={onResume}>继续任务</Button> : undefined}
        />
      )}

      {resultRevisionNotice && !busy && (
        <Alert
          className="creation-result-revision"
          type="info"
          showIcon
          message={resultRevisionNotice}
          action={editedDuringRun ? <Space wrap><Button onClick={onAcceptResult}>接受本次结果</Button><Button type="primary" onClick={onRegenerateLatest}>按最新版重新生成</Button></Space> : undefined}
        />
      )}
    </>
  )
}
