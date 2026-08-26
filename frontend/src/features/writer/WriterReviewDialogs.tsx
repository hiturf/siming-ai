import {
  Alert,
  Button,
  Empty,
  Modal,
  Progress,
  Select,
  Space,
  Tag,
  Typography,
} from 'antd'
import { useMemo } from 'react'

import type { ModelSelectOption } from '../../hooks/useModelOptions'
import { buildTextDiff, type TextDiffPart } from './textDiff'

const { Paragraph, Text, Title } = Typography

export interface DeAiPreview {
  chapter_id: string
  original: string
  input?: string
  rewritten: string
  original_word_count: number
  input_word_count?: number
  rewritten_word_count: number
  provider: string
  model: string
  mutated: false
  persisted?: false
  auto_adopted?: false
  review_required?: true
  revision_round?: number
  max_revision_rounds?: number
  can_continue?: boolean
  audit_passed?: boolean
  candidate_status?: 'ready' | 'review_with_warnings'
  warnings?: Array<{
    source: 'revision_quality' | 'fidelity_audit' | 'style_audit' | string
    code: string
    detail: string
    chunk?: number
  }>
}

export interface QualityScorePreview {
  chapter_id: string
  word_count: number
  total_score: number
  max_score: number
  scores: Array<{
    dimension: string
    score: number
    comment: string
  }>
  ai_flavor_count: number
  overall_assessment: string
  bottom3_improvements: string[]
  provider: string
  model: string
  mutated: false
}

export interface QualityScoreTarget {
  title: string
  content: string
}

export interface DeAiTarget {
  scope: 'chapter' | 'selection'
  baseContent: string
  source: string
  start: number
  end: number
}

interface WriterReviewDialogsProps {
  modelOptions: ModelSelectOption[]
  modelsLoading: boolean
  quality: {
    open: boolean
    loading: boolean
    model?: string
    target: QualityScoreTarget | null
    preview: QualityScorePreview | null
    onModelChange: (model: string) => void
    onClose: () => void
    onGenerate: () => void
  }
  deAi: {
    open: boolean
    loading: boolean
    model?: string
    target: DeAiTarget | null
    preview: DeAiPreview | null
    onModelChange: (model: string) => void
    onClose: () => void
    onGenerate: () => void
    onApply: () => void
  }
}

function qualityScoreLevel(score: number) {
  if (score >= 64) return { label: '表现突出', color: 'success' }
  if (score >= 48) return { label: '基础稳健', color: 'processing' }
  return { label: '建议优先修订', color: 'warning' }
}

function DeAiDiffText({
  parts,
  side,
}: {
  parts: TextDiffPart[]
  side: 'original' | 'candidate'
}) {
  return (
    <pre
      className="writer-de-ai-diff-text"
      aria-label={side === 'original'
        ? '原文差异，红色表示被删除或改写的内容'
        : '候选稿差异，绿色表示新增或改写后的内容'}
    >
      {parts.map((part, index) => {
        if (part.kind === 'equal') {
          return <span key={`${part.kind}-${index}`}>{part.text}</span>
        }
        if (side === 'original' && part.kind === 'removed') {
          return (
            <del className="writer-de-ai-diff-removed" key={`${part.kind}-${index}`}>
              {part.text}
            </del>
          )
        }
        if (side === 'candidate' && part.kind === 'added') {
          return (
            <ins className="writer-de-ai-diff-added" key={`${part.kind}-${index}`}>
              {part.text}
            </ins>
          )
        }
        return null
      })}
    </pre>
  )
}

export function WriterReviewDialogs({
  modelOptions,
  modelsLoading,
  quality,
  deAi,
}: WriterReviewDialogsProps) {
  const deAiDiff = useMemo(() => {
    if (!deAi.preview) return null
    return buildTextDiff(
      deAi.target?.source ?? deAi.preview.original,
      deAi.preview.rewritten,
    )
  }, [deAi.preview, deAi.target?.source])

  return (
    <>
      <Modal
        title="质量评分"
        open={quality.open}
        width={920}
        destroyOnHidden
        maskClosable={!quality.loading}
        keyboard={!quality.loading}
        onCancel={() => { if (!quality.loading) quality.onClose() }}
        footer={[
          <Button key="close" disabled={quality.loading} onClick={quality.onClose}>
            {quality.preview ? '关闭' : '取消'}
          </Button>,
          <Button key="score" type="primary" loading={quality.loading} onClick={quality.onGenerate}>
            {quality.preview ? '重新评分' : '开始评分'}
          </Button>,
        ]}
      >
        <Alert
          type="info"
          showIcon
          message="手动评分只做检查，不会改写或保存正文"
          description="评分以打开窗口时的整章内容为准。你可以根据结果自行修改，再按需重新评分。"
        />
        <div className="writer-quality-toolbar">
          <Space size={8} wrap>
            <Tag>整章</Tag>
            <Text type="secondary">{quality.target?.content.length || 0} 字</Text>
          </Space>
          <div className="writer-quality-model">
            <Text type="secondary">评分模型</Text>
            <Select
              value={quality.model}
              options={modelOptions}
              loading={modelsLoading}
              disabled={quality.loading}
              onChange={quality.onModelChange}
              placeholder="选择 API 或本机 CLI 模型"
              optionFilterProp="label"
              showSearch
            />
          </div>
        </div>
        {!quality.preview ? (
          <div className="writer-quality-empty">
            <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="选择模型后点击“开始评分”" />
          </div>
        ) : (
          <div className="writer-quality-result" aria-live="polite">
            <section className="writer-quality-summary">
              <div
                className="writer-quality-score"
                aria-label={`质量总分 ${quality.preview.total_score} 分，共 ${quality.preview.max_score} 分`}
              >
                <strong>{quality.preview.total_score}</strong>
                <span>/{quality.preview.max_score}</span>
              </div>
              <div className="writer-quality-overview">
                <Space size={8} wrap>
                  <Tag color={qualityScoreLevel(quality.preview.total_score).color}>
                    {qualityScoreLevel(quality.preview.total_score).label}
                  </Tag>
                  <Tag color={quality.preview.ai_flavor_count > 0 ? 'orange' : 'green'}>
                    AI 味线索 {quality.preview.ai_flavor_count} 处
                  </Tag>
                  <Text type="secondary">{quality.preview.model || quality.preview.provider}</Text>
                </Space>
                <Paragraph>{quality.preview.overall_assessment}</Paragraph>
              </div>
            </section>
            <section>
              <Title level={5}>分项评分</Title>
              <div className="writer-quality-dimensions">
                {quality.preview.scores.map((item) => (
                  <div className="writer-quality-dimension" key={item.dimension}>
                    <div className="writer-quality-dimension-head">
                      <Text strong>{item.dimension}</Text>
                      <Text>{item.score}/10</Text>
                    </div>
                    <Progress percent={item.score * 10} showInfo={false} size="small" />
                    <Text type="secondary">{item.comment}</Text>
                  </div>
                ))}
              </div>
            </section>
            <section className="writer-quality-improvements">
              <Title level={5}>优先改进</Title>
              {quality.preview.bottom3_improvements.length > 0 ? (
                <ol>
                  {quality.preview.bottom3_improvements.map((item, index) => (
                    <li key={`${item}-${index}`}>{item}</li>
                  ))}
                </ol>
              ) : (
                <Text type="secondary">本次评分没有返回明确的优先改进项。</Text>
              )}
            </section>
          </div>
        )}
      </Modal>

      <Modal
        title="去除 AI 味"
        open={deAi.open}
        width={1040}
        destroyOnHidden
        maskClosable={!deAi.loading}
        keyboard={!deAi.loading}
        onCancel={() => { if (!deAi.loading) deAi.onClose() }}
        footer={[
          <Button key="cancel" disabled={deAi.loading} onClick={deAi.onClose}>取消</Button>,
          deAi.preview && (deAi.preview.can_continue ?? ((deAi.preview.revision_round || 1) < 3)) ? (
            <Button key="continue" loading={deAi.loading} onClick={deAi.onGenerate}>
              继续处理候选稿（第 {Math.min((deAi.preview.revision_round || 1) + 1, 3)}/3 轮）
            </Button>
          ) : null,
          deAi.preview ? (
            <Button key="apply" type="primary" disabled={deAi.loading} onClick={deAi.onApply}>
              {deAi.preview.audit_passed === false ? '仍要替换到编辑器' : '替换到编辑器'}
            </Button>
          ) : (
            <Button key="generate" type="primary" loading={deAi.loading} onClick={deAi.onGenerate}>生成候选稿</Button>
          ),
        ]}
      >
        <Alert
          type="info"
          showIcon
          message="这是一项独立修订，任何审核结果都不会自动覆盖正文"
          description="原文和候选稿会同时显示。只有你明确选择替换，候选稿才会进入编辑器；之后仍需手动保存。"
        />
        {deAi.preview?.audit_passed === false && (
          <Alert
            type="warning"
            showIcon
            message={`候选稿有 ${deAi.preview.warnings?.length || 1} 项系统审核提醒，但仍完整保留供你查看`}
            description={(
              <div>
                <div>系统未采用、未保存这份候选稿。请对照原文判断是否接受：</div>
                <ul>
                  {(deAi.preview.warnings || []).map((warning, index) => (
                    <li key={`${warning.source}-${warning.code}-${index}`}>
                      {warning.chunk ? `第 ${warning.chunk} 段：` : ''}{warning.detail}
                    </li>
                  ))}
                </ul>
              </div>
            )}
          />
        )}
        <div className="writer-de-ai-toolbar">
          <Space size={8} wrap>
            <Tag color={deAi.target?.scope === 'selection' ? 'blue' : 'default'}>
              {deAi.target?.scope === 'selection' ? '选中段落' : '整章'}
            </Tag>
            <Text type="secondary">
              原文 {deAi.preview?.original_word_count ?? deAi.target?.source.length ?? 0} 字
            </Text>
            {deAi.preview && (
              <Tag color="purple">
                第 {deAi.preview.revision_round || 1}/{deAi.preview.max_revision_rounds || 3} 轮
              </Tag>
            )}
          </Space>
          <div className="writer-de-ai-model">
            <Text type="secondary">执行模型</Text>
            <Select
              value={deAi.model}
              options={modelOptions}
              loading={modelsLoading}
              disabled={deAi.loading}
              onChange={deAi.onModelChange}
              placeholder="选择 API 或本机 CLI 模型"
              optionFilterProp="label"
              showSearch
            />
          </div>
        </div>
        {deAi.preview && (
          <div className="writer-de-ai-diff-legend" aria-label="差异颜色说明">
            <span className="writer-de-ai-legend-removed">
              <i aria-hidden="true" />红色：原文删改
            </span>
            <span className="writer-de-ai-legend-added">
              <i aria-hidden="true" />绿色：候选稿新增
            </span>
          </div>
        )}
        <div className={`writer-de-ai-compare${deAi.preview ? '' : ' writer-de-ai-compare-single'}`} aria-live="polite">
          <section className={`writer-de-ai-pane${deAi.preview ? ' writer-de-ai-pane-original-diff' : ''}`}>
            <div className="writer-de-ai-pane-head"><Text strong>原文（未变更）</Text></div>
            {deAiDiff
              ? <DeAiDiffText parts={deAiDiff} side="original" />
              : <pre>{deAi.target?.source || ''}</pre>}
          </section>
          {deAi.preview && deAiDiff && (
            <section className="writer-de-ai-pane writer-de-ai-pane-candidate">
              <div className="writer-de-ai-pane-head">
                <Text strong>
                  候选稿（第 {deAi.preview.revision_round || 1} 轮，未采用）
                </Text>
                <Text type="secondary">
                  {deAi.preview.rewritten_word_count} 字 · {deAi.preview.model || deAi.preview.provider}
                </Text>
              </div>
              <DeAiDiffText parts={deAiDiff} side="candidate" />
            </section>
          )}
        </div>
      </Modal>
    </>
  )
}
