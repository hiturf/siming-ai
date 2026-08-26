import { PersistentOutcome, RecoveryPanel } from '../interaction'

export function StageFeedback({
  currentStage,
  status,
  hasData,
  staleReason,
  error,
  recommendedStageLabel,
  canRetryNext,
  onRetryNext,
}: {
  currentStage: string
  status?: string
  hasData: boolean
  staleReason?: string
  error?: string
  recommendedStageLabel: string
  canRetryNext: boolean
  onRetryNext: () => void
}) {
  return (
    <>
      {status === 'generated' && hasData && (
        <PersistentOutcome
          className="creation-stage-outcome"
          outcome="waiting_user"
          title={currentStage === 'final_review' ? '最终审阅已生成，等待你创建正式作品' : '生成完成，等待你确认'}
          description="内容已保存到立项草稿。你可以阅读、修改、确认，也可以先生成其他阶段。"
        />
      )}
      {status === 'stale' && (
        <PersistentOutcome
          className="creation-stage-outcome"
          outcome="blocked"
          title="上游内容已变化，本阶段需要重新校验"
          description={staleReason || '请检查内容后重新生成或编辑，再完成确认。'}
        />
      )}
      {error && (
        <RecoveryPanel
          title="下一步没有启动"
          description={error}
          retryLabel={`重试生成${recommendedStageLabel}`}
          onRetry={canRetryNext ? onRetryNext : undefined}
        />
      )}
    </>
  )
}
