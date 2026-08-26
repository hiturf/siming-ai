import { useEffect, useId, useState } from 'react'
import { BulbOutlined, DownOutlined, LoadingOutlined } from '@ant-design/icons'
import './ReasoningDisclosure.css'

const CHARACTER_INTERVAL_MS = 12

function StreamingReasoningText({ content, streaming }: { content: string; streaming: boolean }) {
  const [visibleContent, setVisibleContent] = useState(() => (streaming ? '' : content))

  useEffect(() => {
    if (!content.startsWith(visibleContent)) {
      setVisibleContent(streaming ? '' : content)
      return
    }
    if (visibleContent.length >= content.length) return

    const timer = window.setTimeout(() => {
      setVisibleContent((current) => {
        if (!content.startsWith(current)) return streaming ? '' : content
        const nextCharacter = Array.from(content.slice(current.length))[0]
        return nextCharacter ? `${current}${nextCharacter}` : current
      })
    }, CHARACTER_INTERVAL_MS)
    return () => window.clearTimeout(timer)
  }, [content, streaming, visibleContent])

  const typing = streaming || visibleContent.length < content.length
  return (
    <div className="assistant-reasoning-text">
      {visibleContent}
      {typing && <span className="assistant-reasoning-caret" aria-hidden="true" />}
    </div>
  )
}

export interface ReasoningDisclosureProps {
  content?: string | null
  streaming?: boolean
}

/** Displays only reasoning text explicitly returned by the configured model API. */
export function ReasoningDisclosure({
  content,
  streaming = false,
}: ReasoningDisclosureProps) {
  const normalizedContent = String(content || '').trim()
  const [expanded, setExpanded] = useState(streaming)
  const contentId = useId()

  if (!normalizedContent) return null

  return (
    <section className={`assistant-reasoning${streaming ? ' assistant-reasoning-streaming' : ''}`}>
      <button
        type="button"
        className="assistant-reasoning-trigger"
        aria-controls={contentId}
        aria-expanded={expanded}
        onClick={() => setExpanded((value) => !value)}
      >
        <span className="assistant-reasoning-title">
          <BulbOutlined aria-hidden="true" />
          模型思考摘要
        </span>
        <span className="assistant-reasoning-state">
          {streaming ? (
            <>
              <LoadingOutlined spin aria-hidden="true" />
              实时生成
            </>
          ) : '已完成'}
          <DownOutlined className="assistant-reasoning-chevron" aria-hidden="true" />
        </span>
      </button>
      <div
        className={`assistant-reasoning-region${expanded ? ' assistant-reasoning-region-open' : ''}`}
        aria-hidden={!expanded}
      >
        <div className="assistant-reasoning-region-inner">
          <div id={contentId} className="assistant-reasoning-body">
            <div className="assistant-reasoning-note">仅展示模型 API 实际返回的可见推理内容</div>
            <StreamingReasoningText content={normalizedContent} streaming={streaming} />
          </div>
        </div>
      </div>
    </section>
  )
}
