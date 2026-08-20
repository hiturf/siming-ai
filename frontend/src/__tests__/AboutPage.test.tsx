import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const { mockApiGet } = vi.hoisted(() => ({ mockApiGet: vi.fn() }))

vi.mock('../api/client', () => ({ apiClient: { get: mockApiGet } }))

import AboutPage from '../pages/AboutPage'

describe('AboutPage', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockApiGet.mockResolvedValue({
      data: { data: { name: '司命', version: '3.3.0' } },
    })
  })

  it('presents the product identity, principles, privacy boundary, and official resources', async () => {
    render(<MemoryRouter initialEntries={['/about']}><AboutPage /></MemoryRouter>)

    expect(screen.getByRole('heading', { level: 1, name: /让长篇故事/ })).toBeInTheDocument()
    expect(screen.getByText('作品属于作者')).toBeInTheDocument()
    expect(screen.getByText('事实先于生成')).toBeInTheDocument()
    expect(screen.getByText('边界必须透明')).toBeInTheDocument()
    expect(screen.getByText(/司命没有官方小说数据云服务/)).toBeInTheDocument()
    expect(screen.getByText('Apache 2.0')).toBeInTheDocument()
    expect(screen.getByText('814283606')).toBeInTheDocument()
    const versionLabels = await screen.findAllByLabelText('司命版本 3.3.0')
    expect(versionLabels).toHaveLength(2)
    expect(versionLabels[1]).toHaveTextContent('v3.3.0')

    expect(screen.getByRole('link', { name: /查看开源项目/ })).toHaveAttribute(
      'href',
      'https://github.com/teangtang1122/siming-ai',
    )
    expect(screen.getByRole('link', { name: /反馈问题/ })).toHaveAttribute(
      'href',
      'https://github.com/teangtang1122/siming-ai/issues/new/choose',
    )
    expect(screen.getByRole('button', { name: '关于我们' })).toHaveAttribute('aria-current', 'page')
  })
})
