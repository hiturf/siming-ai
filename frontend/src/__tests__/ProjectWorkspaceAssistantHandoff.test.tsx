import { render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'

vi.mock('../features/projects', () => ({
  useProject: () => ({
    data: { id: 'project-1', title: '灰港遗忘症' },
    isError: false,
    error: null,
    refetch: vi.fn(),
  }),
}))

vi.mock('../components/AiSidePanel', () => ({
  default: ({ collapsed, children }: { collapsed: boolean; children: React.ReactNode }) => (
    <aside data-testid="project-assistant-panel" data-collapsed={String(collapsed)}>{children}</aside>
  ),
}))

vi.mock('../components/TabCache', () => ({ default: () => null }))
vi.mock('../components/WorkspaceAssistantChat', () => ({ default: () => <div>项目助手内容</div> }))
vi.mock('../themes/ThemeSwitcher', () => ({ default: () => null }))
vi.mock('../contexts/AiPanelContext', () => ({
  AiPanelProvider: ({ children }: { children: React.ReactNode }) => <>{children}</>,
  useAiPanelContext: () => ({
    selectedText: undefined,
    selectedTextChapterId: undefined,
    triggerRefresh: vi.fn(),
  }),
}))
vi.mock('../hooks/useModelOptions', () => ({
  useModelOptions: () => ({ modelOptions: [], defaultModel: undefined, loading: false, setGlobalModel: vi.fn() }),
}))
vi.mock('../hooks/usePanelResize', () => ({
  usePanelResize: () => ({ width: 360, onDragHandleMouseDown: vi.fn(), dragging: false }),
}))

import ProjectWorkspace from '../pages/ProjectWorkspace'

function LocationProbe() {
  const location = useLocation()
  return <output data-testid="location">{location.pathname}{location.search}</output>
}

describe('ProjectWorkspace formal-project handoff', () => {
  beforeEach(() => {
    localStorage.clear()
    localStorage.setItem('siming_ai_panel_collapsed', 'true')
  })

  it('opens the project assistant and consumes the one-shot handoff query', async () => {
    render(
      <MemoryRouter initialEntries={['/project/project-1?assistant=open']}>
        <Routes>
          <Route
            path="/project/:projectId"
            element={<><ProjectWorkspace /><LocationProbe /></>}
          />
        </Routes>
      </MemoryRouter>,
    )

    expect(await screen.findByRole('button', { name: '收起项目助手' })).toHaveAttribute('aria-expanded', 'true')
    expect(screen.getByTestId('project-assistant-panel')).toHaveAttribute('data-collapsed', 'false')
    expect(screen.getByText('项目助手内容')).toBeInTheDocument()
    await waitFor(() => expect(screen.getByTestId('location')).toHaveTextContent('/project/project-1'))
    expect(screen.getByTestId('location')).not.toHaveTextContent('assistant=open')
    expect(localStorage.getItem('siming_ai_panel_collapsed')).toBe('false')
  })
})
