export type TextDiffKind = 'equal' | 'removed' | 'added'

export interface TextDiffPart {
  kind: TextDiffKind
  text: string
}

interface SegmenterLike {
  segment: (input: string) => Iterable<{ segment: string }>
}

type SegmenterConstructor = new (
  locales?: string | string[],
  options?: { granularity: 'word' },
) => SegmenterLike

interface DiffStep {
  kind: TextDiffKind
  token: string
}

// A bounded edit distance keeps a radically rewritten long chapter from
// monopolising the browser. In that rare case the unchanged edges remain
// precise and the rewritten middle is shown as one removed/added region.
const MAX_EDIT_DISTANCE = 1200

function tokenize(text: string): string[] {
  const Segmenter = (Intl as typeof Intl & { Segmenter?: SegmenterConstructor }).Segmenter
  if (!Segmenter) return Array.from(text)

  return Array.from(
    new Segmenter('zh-CN', { granularity: 'word' }).segment(text),
    ({ segment }) => segment,
  )
}

function frontierValue(frontier: Int32Array, distance: number, diagonal: number): number {
  if (Math.abs(diagonal) > distance || (diagonal + distance) % 2 !== 0) return -1
  return frontier[diagonal + distance]
}

function backtrack(
  trace: Int32Array[],
  original: string[],
  rewritten: string[],
): DiffStep[] {
  let originalIndex = original.length
  let rewrittenIndex = rewritten.length
  const reversed: DiffStep[] = []

  for (let distance = trace.length - 1; distance > 0; distance -= 1) {
    const previousFrontier = trace[distance - 1]
    const diagonal = originalIndex - rewrittenIndex
    const left = frontierValue(previousFrontier, distance - 1, diagonal - 1)
    const right = frontierValue(previousFrontier, distance - 1, diagonal + 1)
    const previousDiagonal = diagonal === -distance
      || (diagonal !== distance && left < right)
      ? diagonal + 1
      : diagonal - 1
    const previousOriginalIndex = frontierValue(
      previousFrontier,
      distance - 1,
      previousDiagonal,
    )
    const previousRewrittenIndex = previousOriginalIndex - previousDiagonal

    while (
      originalIndex > previousOriginalIndex
      && rewrittenIndex > previousRewrittenIndex
    ) {
      reversed.push({ kind: 'equal', token: original[originalIndex - 1] })
      originalIndex -= 1
      rewrittenIndex -= 1
    }

    if (originalIndex === previousOriginalIndex) {
      reversed.push({ kind: 'added', token: rewritten[rewrittenIndex - 1] })
      rewrittenIndex -= 1
    } else {
      reversed.push({ kind: 'removed', token: original[originalIndex - 1] })
      originalIndex -= 1
    }
  }

  while (originalIndex > 0 && rewrittenIndex > 0) {
    reversed.push({ kind: 'equal', token: original[originalIndex - 1] })
    originalIndex -= 1
    rewrittenIndex -= 1
  }
  while (originalIndex > 0) {
    reversed.push({ kind: 'removed', token: original[originalIndex - 1] })
    originalIndex -= 1
  }
  while (rewrittenIndex > 0) {
    reversed.push({ kind: 'added', token: rewritten[rewrittenIndex - 1] })
    rewrittenIndex -= 1
  }

  return reversed.reverse()
}

function shortestEditScript(original: string[], rewritten: string[]): DiffStep[] | null {
  const largestDistance = Math.min(
    original.length + rewritten.length,
    MAX_EDIT_DISTANCE,
  )
  const trace: Int32Array[] = []

  for (let distance = 0; distance <= largestDistance; distance += 1) {
    const frontier = new Int32Array(distance * 2 + 1)
    frontier.fill(-1)

    for (let diagonal = -distance; diagonal <= distance; diagonal += 2) {
      let originalIndex = 0
      if (distance > 0) {
        const previousFrontier = trace[distance - 1]
        const left = frontierValue(previousFrontier, distance - 1, diagonal - 1)
        const right = frontierValue(previousFrontier, distance - 1, diagonal + 1)
        originalIndex = diagonal === -distance
          || (diagonal !== distance && left < right)
          ? right
          : left + 1
      }

      let rewrittenIndex = originalIndex - diagonal
      while (
        originalIndex < original.length
        && rewrittenIndex < rewritten.length
        && original[originalIndex] === rewritten[rewrittenIndex]
      ) {
        originalIndex += 1
        rewrittenIndex += 1
      }
      frontier[diagonal + distance] = originalIndex

      if (originalIndex >= original.length && rewrittenIndex >= rewritten.length) {
        trace.push(frontier)
        return backtrack(trace, original, rewritten)
      }
    }

    trace.push(frontier)
  }

  return null
}

function appendPart(parts: TextDiffPart[], kind: TextDiffKind, text: string) {
  if (!text) return
  const last = parts[parts.length - 1]
  if (last?.kind === kind) {
    last.text += text
    return
  }
  parts.push({ kind, text })
}

export function buildTextDiff(original: string, rewritten: string): TextDiffPart[] {
  if (original === rewritten) return original ? [{ kind: 'equal', text: original }] : []

  const originalTokens = tokenize(original)
  const rewrittenTokens = tokenize(rewritten)
  const sharedLimit = Math.min(originalTokens.length, rewrittenTokens.length)
  let prefixLength = 0
  while (
    prefixLength < sharedLimit
    && originalTokens[prefixLength] === rewrittenTokens[prefixLength]
  ) {
    prefixLength += 1
  }

  let suffixLength = 0
  while (
    suffixLength < sharedLimit - prefixLength
    && originalTokens[originalTokens.length - suffixLength - 1]
      === rewrittenTokens[rewrittenTokens.length - suffixLength - 1]
  ) {
    suffixLength += 1
  }

  const originalMiddle = originalTokens.slice(
    prefixLength,
    originalTokens.length - suffixLength,
  )
  const rewrittenMiddle = rewrittenTokens.slice(
    prefixLength,
    rewrittenTokens.length - suffixLength,
  )
  const parts: TextDiffPart[] = []

  appendPart(parts, 'equal', originalTokens.slice(0, prefixLength).join(''))
  const editSteps = shortestEditScript(originalMiddle, rewrittenMiddle)
  if (editSteps) {
    editSteps.forEach(({ kind, token }) => appendPart(parts, kind, token))
  } else {
    appendPart(parts, 'removed', originalMiddle.join(''))
    appendPart(parts, 'added', rewrittenMiddle.join(''))
  }
  appendPart(
    parts,
    'equal',
    originalTokens.slice(originalTokens.length - suffixLength).join(''),
  )

  return parts
}
