import { fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter, useLocation } from 'react-router-dom'
import { describe, expect, it } from 'vitest'

import { MessageList } from '../components/assistant/MessageList'
import type { WorkspaceAssistantMessage } from '../components/assistant/types'

function LocationProbe() {
  const location = useLocation()
  return <output data-testid="location">{`${location.pathname}${location.search}`}</output>
}

describe('MessageList cataloging navigation action', () => {
  it('navigates to the cataloging page from the reminder button', () => {
    const messages: WorkspaceAssistantMessage[] = [{
      id: 'cataloging-operation-job-1-started',
      role: 'assistant',
      content: '《第二章 吐纳》已保存，建档已经开始。',
      status: 'running',
      created_at: '2026-08-14T15:30:00',
      navigation_action: {
        label: '查看建档进度',
        to: '/project/project-1?view=cataloging',
      },
    }]

    render(
      <MemoryRouter initialEntries={['/project/project-1?view=writer']}>
        <MessageList
          messages={messages}
          generating={false}
          showScrollBottom={false}
          onScrollToBottom={() => undefined}
          messagesRef={{ current: null }}
          onScroll={() => undefined}
          projectId="project-1"
        />
        <LocationProbe />
      </MemoryRouter>,
    )

    fireEvent.click(screen.getByRole('button', { name: /查看建档进度/ }))

    expect(screen.getByTestId('location')).toHaveTextContent('/project/project-1?view=cataloging')
    expect(document.querySelector('time')).toHaveAttribute('datetime', '2026-08-14T15:30:00.000Z')
  })
})
