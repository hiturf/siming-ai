import { createContext, useCallback, useContext, useMemo, useState } from 'react'

interface AiSelectionState {
  selectedText: string
  selectedTextChapterId: string | null
}

export interface GeneratedChapterDraft {
  draftId: string
  projectId: string
  title: string
  outlineNodeId: string | null
  contextManifestId: string | null
  savedChapterId: string | null
  content: string
  wordCount: number
  status: 'pending' | 'saved' | 'superseded'
}

interface AiPanelContextValue extends AiSelectionState {
  setAiContext: (partial: Partial<AiSelectionState>) => void
  generatedDraft: GeneratedChapterDraft | null
  openGeneratedDraft: (draft: GeneratedChapterDraft) => void
  updateGeneratedDraft: (partial: Partial<GeneratedChapterDraft>) => void
  clearGeneratedDraft: () => void
  refreshKey: number
  triggerRefresh: () => void
}

const AiPanelContext = createContext<AiPanelContextValue>({
  selectedText: '',
  selectedTextChapterId: null,
  setAiContext: () => {},
  generatedDraft: null,
  openGeneratedDraft: () => {},
  updateGeneratedDraft: () => {},
  clearGeneratedDraft: () => {},
  refreshKey: 0,
  triggerRefresh: () => {},
})

export function AiPanelProvider({ children }: { children: React.ReactNode }) {
  const [context, setContext] = useState<AiSelectionState>({
    selectedText: '',
    selectedTextChapterId: null,
  })
  const [generatedDraft, setGeneratedDraft] = useState<GeneratedChapterDraft | null>(null)
  const [refreshKey, setRefreshKey] = useState(0)

  const setAiContext = useCallback(
    (partial: Partial<AiSelectionState>) => {
      setContext((prev) => ({ ...prev, ...partial }))
    },
    [],
  )

  const openGeneratedDraft = useCallback((draft: GeneratedChapterDraft) => {
    setGeneratedDraft(draft)
  }, [])

  const updateGeneratedDraft = useCallback((partial: Partial<GeneratedChapterDraft>) => {
    setGeneratedDraft((current) => current ? { ...current, ...partial } : current)
  }, [])

  const clearGeneratedDraft = useCallback(() => setGeneratedDraft(null), [])

  const triggerRefresh = useCallback(() => {
    setRefreshKey((key) => key + 1)
  }, [])

  const value = useMemo<AiPanelContextValue>(
    () => ({
      ...context,
      setAiContext,
      generatedDraft,
      openGeneratedDraft,
      updateGeneratedDraft,
      clearGeneratedDraft,
      refreshKey,
      triggerRefresh,
    }),
    [
      context,
      setAiContext,
      generatedDraft,
      openGeneratedDraft,
      updateGeneratedDraft,
      clearGeneratedDraft,
      refreshKey,
      triggerRefresh,
    ],
  )

  return <AiPanelContext.Provider value={value}>{children}</AiPanelContext.Provider>
}

export function useAiPanelContext() {
  return useContext(AiPanelContext)
}

export default AiPanelContext
