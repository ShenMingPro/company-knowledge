import { afterEach, describe, expect, it, vi } from 'vitest'

import { authApi, chatApi } from './api'

describe('authApi.logout', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('accepts a successful 204 response with an empty body', async () => {
    vi.stubGlobal('localStorage', {
      getItem: vi.fn(() => 'access-token'),
      removeItem: vi.fn(),
    })
    const fetchMock = vi.fn(async () => new Response(null, {
      status: 204,
      headers: { 'Content-Type': 'application/json' },
    }))
    vi.stubGlobal('fetch', fetchMock)

    await expect(authApi.logout()).resolves.toBeUndefined()
    expect(fetchMock).toHaveBeenCalledOnce()
  })
})

describe('chatApi.askStream', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('parses SSE events incrementally across response chunks', async () => {
    vi.stubGlobal('localStorage', {
      getItem: vi.fn(() => 'access-token'),
      removeItem: vi.fn(),
    })
    const encoder = new TextEncoder()
    const firstEvent = encoder.encode('event: delta\ndata: {"delta":"你好"}\n\n')
    const chineseStart = encoder.encode('event: delta\ndata: {"delta":"').length
    const body = new ReadableStream({
      start(controller) {
        // Split inside the three-byte UTF-8 encoding of "你".
        controller.enqueue(firstEvent.slice(0, chineseStart + 1))
        controller.enqueue(firstEvent.slice(chineseStart + 1))
        controller.enqueue(encoder.encode('event: delta\ndata: {"delta":"！"}\n\n'))
        controller.enqueue(encoder.encode('event: done\ndata: {"answer":"你好！","session_id":3,"message_id":8,"references":[]}\n\n'))
        controller.close()
      },
    })
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) => new Response(body, {
      headers: { 'Content-Type': 'text/event-stream; charset=utf-8' },
    }))
    vi.stubGlobal('fetch', fetchMock)

    const events = []
    for await (const event of chatApi.askStream('你好')) events.push(event)

    expect(events).toEqual([
      { type: 'delta', content: '你好' },
      { type: 'delta', content: '！' },
      { type: 'done', answer: '你好！', session_id: 3, message_id: 8, references: [] },
    ])
    expect(fetchMock).toHaveBeenCalledWith('/api/chat/query/stream', expect.objectContaining({
      method: 'POST',
      headers: expect.any(Headers),
    }))
    const headers = fetchMock.mock.calls[0][1]?.headers as Headers
    expect(headers.get('Authorization')).toBe('Bearer access-token')
    expect(headers.get('Accept')).toBe('text/event-stream')
  })

  it('normalizes an SSE error event', async () => {
    vi.stubGlobal('localStorage', { getItem: vi.fn(() => null), removeItem: vi.fn() })
    vi.stubGlobal('fetch', vi.fn(async () => new Response(
      'event: error\ndata: {"detail":"模型暂不可用"}\n\n',
      { headers: { 'Content-Type': 'text/event-stream' } },
    )))

    const events = []
    for await (const event of chatApi.askStream('测试')) events.push(event)

    expect(events).toEqual([{ type: 'error', message: '模型暂不可用', detail: { detail: '模型暂不可用' } }])
  })
})
