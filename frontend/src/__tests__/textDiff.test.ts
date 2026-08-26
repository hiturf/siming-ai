import { describe, expect, it } from 'vitest'

import { buildTextDiff } from '../features/writer/textDiff'

function rebuildOriginal(parts: ReturnType<typeof buildTextDiff>) {
  return parts
    .filter((part) => part.kind !== 'added')
    .map((part) => part.text)
    .join('')
}

function rebuildCandidate(parts: ReturnType<typeof buildTextDiff>) {
  return parts
    .filter((part) => part.kind !== 'removed')
    .map((part) => part.text)
    .join('')
}

describe('buildTextDiff', () => {
  it('marks Chinese removals and additions while preserving both full texts', () => {
    const original = '他缓缓推开门。屋里没有人。'
    const candidate = '他推开门。屋里只剩一盏灯。'
    const parts = buildTextDiff(original, candidate)

    expect(rebuildOriginal(parts)).toBe(original)
    expect(rebuildCandidate(parts)).toBe(candidate)
    expect(parts.some((part) => part.kind === 'removed')).toBe(true)
    expect(parts.some((part) => part.kind === 'added')).toBe(true)
  })

  it('returns one unchanged part for identical content', () => {
    expect(buildTextDiff('门外在下雨。', '门外在下雨。')).toEqual([
      { kind: 'equal', text: '门外在下雨。' },
    ])
  })

  it('handles content that is only inserted or removed', () => {
    expect(rebuildCandidate(buildTextDiff('', '新增段落'))).toBe('新增段落')
    expect(rebuildOriginal(buildTextDiff('删去段落', ''))).toBe('删去段落')
  })
})
