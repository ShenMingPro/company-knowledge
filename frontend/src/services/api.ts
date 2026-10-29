import type {
  AuditLog,
  ChatReply,
  ChatStreamEvent,
  ChatSession,
  DocumentTaskSubmission,
  DocumentItem,
  KnowledgeSpace,
  Paginated,
  StoredExchange,
  TaskStatus,
  User,
} from '@/types/api'

const API_PREFIX = import.meta.env.VITE_API_PREFIX || '/api'
const TOKEN_KEY = 'knowbase_access_token'

type QueryValue = string | number | boolean | null | undefined

export class ApiError extends Error {
  status: number
  detail?: unknown

  constructor(message: string, status: number, detail?: unknown) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.detail = detail
  }
}

function queryString(params?: Record<string, QueryValue>) {
  if (!params) return ''
  const search = new URLSearchParams()
  Object.entries(params).forEach(([key, value]) => {
    if (value !== undefined && value !== null && value !== '') search.set(key, String(value))
  })
  const result = search.toString()
  return result ? `?${result}` : ''
}

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const token = localStorage.getItem(TOKEN_KEY)
  const headers = new Headers(options.headers)
  if (!(options.body instanceof FormData) && options.body !== undefined) {
    headers.set('Content-Type', 'application/json')
  }
  if (token) headers.set('Authorization', `Bearer ${token}`)

  let response: Response
  try {
    response = await fetch(`${API_PREFIX}${path}`, { ...options, headers })
  } catch {
    throw new ApiError('无法连接服务，请确认 API 已启动', 0)
  }

  const isJson = response.headers.get('content-type')?.includes('application/json')
  const hasNoBody = response.status === 204 || response.status === 205
  const responseText = hasNoBody ? '' : await response.text()
  const data = responseText
    ? (isJson ? JSON.parse(responseText) : responseText)
    : undefined

  if (!response.ok) {
    if (response.status === 401) {
      localStorage.removeItem(TOKEN_KEY)
      localStorage.removeItem('knowbase_user')
      window.dispatchEvent(new CustomEvent('auth:expired'))
    }
    const message = typeof data === 'object' && data
      ? data.detail || data.message || data.error
      : data
    throw new ApiError(typeof message === 'string' ? message : '请求失败，请稍后重试', response.status, data)
  }
  return data as T
}

function expireAuthentication() {
  localStorage.removeItem(TOKEN_KEY)
  localStorage.removeItem('knowbase_user')
  window.dispatchEvent(new CustomEvent('auth:expired'))
}

function streamEvent(eventName: string, rawData: string): ChatStreamEvent | undefined {
  if (!rawData.trim()) return undefined

  let parsed: unknown
  try {
    parsed = JSON.parse(rawData)
  } catch {
    if (eventName === 'delta') return { type: 'delta', content: rawData }
    throw new ApiError('服务返回了无效的流式数据', 0, rawData)
  }

  if (!parsed || typeof parsed !== 'object') {
    throw new ApiError('服务返回了无效的流式数据', 0, parsed)
  }

  const record = parsed as Record<string, unknown>
  const type = eventName === 'message' && typeof record.type === 'string' ? record.type : eventName
  const nested = record.data && typeof record.data === 'object'
    ? record.data as Record<string, unknown>
    : record

  if (type === 'delta') {
    const content = nested.delta ?? nested.content
    if (typeof content !== 'string') throw new ApiError('流式回答缺少文本内容', 0, parsed)
    return { type: 'delta', content }
  }
  if (type === 'meta') return { type: 'meta', ...nested } as ChatStreamEvent
  if (type === 'done') return { type: 'done', ...nested } as ChatStreamEvent
  if (type === 'error') {
    const detail = nested.detail ?? nested.message ?? nested.error
    return {
      type: 'error',
      message: typeof detail === 'string' ? detail : '回答生成失败，请稍后重试',
      detail: parsed,
    }
  }
  return undefined
}

function parseSseBlock(block: string): ChatStreamEvent | undefined {
  let eventName = 'message'
  const data: string[] = []
  for (const line of block.split(/\r?\n/)) {
    if (!line || line.startsWith(':')) continue
    const separator = line.indexOf(':')
    const field = separator < 0 ? line : line.slice(0, separator)
    let value = separator < 0 ? '' : line.slice(separator + 1)
    if (value.startsWith(' ')) value = value.slice(1)
    if (field === 'event') eventName = value
    if (field === 'data') data.push(value)
  }
  return streamEvent(eventName, data.join('\n'))
}

async function* streamRequest(path: string, options: RequestInit): AsyncGenerator<ChatStreamEvent> {
  const token = localStorage.getItem(TOKEN_KEY)
  const headers = new Headers(options.headers)
  headers.set('Content-Type', 'application/json')
  headers.set('Accept', 'text/event-stream')
  if (token) headers.set('Authorization', `Bearer ${token}`)

  let response: Response
  try {
    response = await fetch(`${API_PREFIX}${path}`, { ...options, headers })
  } catch {
    throw new ApiError('无法连接服务，请确认 API 已启动', 0)
  }

  if (!response.ok) {
    const responseText = await response.text()
    let detail: unknown = responseText
    try {
      detail = responseText ? JSON.parse(responseText) : undefined
    } catch {
      // Keep a plain-text error body as-is.
    }
    if (response.status === 401) expireAuthentication()
    const record = detail && typeof detail === 'object' ? detail as Record<string, unknown> : undefined
    const message = record?.detail ?? record?.message ?? record?.error ?? detail
    throw new ApiError(typeof message === 'string' ? message : '请求失败，请稍后重试', response.status, detail)
  }
  if (!response.body) throw new ApiError('浏览器无法读取流式响应', 0)

  const contentType = response.headers.get('content-type') || ''
  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''

  while (true) {
    const { done, value } = await reader.read()
    buffer += decoder.decode(value, { stream: !done })

    if (contentType.includes('text/event-stream')) {
      let separator = buffer.match(/\r?\n\r?\n/)
      while (separator?.index !== undefined) {
        const block = buffer.slice(0, separator.index)
        buffer = buffer.slice(separator.index + separator[0].length)
        const event = parseSseBlock(block)
        if (event) yield event
        separator = buffer.match(/\r?\n\r?\n/)
      }
    } else {
      let newline = buffer.indexOf('\n')
      while (newline >= 0) {
        const line = buffer.slice(0, newline).trim()
        buffer = buffer.slice(newline + 1)
        if (line) {
          const parsed = JSON.parse(line) as Record<string, unknown>
          const event = streamEvent(typeof parsed.type === 'string' ? parsed.type : 'message', line)
          if (event) yield event
        }
        newline = buffer.indexOf('\n')
      }
    }
    if (done) break
  }

  const trailing = buffer.trim()
  if (trailing) {
    const event = contentType.includes('text/event-stream')
      ? parseSseBlock(trailing)
      : streamEvent('message', trailing)
    if (event) yield event
  }
}

function normalizeList<T>(data: unknown, keys: string[]): T[] {
  if (Array.isArray(data)) return data as T[]
  if (data && typeof data === 'object') {
    const record = data as Record<string, unknown>
    for (const key of keys) if (Array.isArray(record[key])) return record[key] as T[]
  }
  return []
}

export const authApi = {
  async login(username: string, password: string) {
    const data = await request<{ access_token?: string; token?: string; user: User; token_type?: string }>('/auth/login', {
      method: 'POST',
      body: JSON.stringify({ username, password }),
    })
    return { token: data.access_token || data.token || '', user: data.user }
  },
  me: () => request<User>('/auth/me'),
  logout: () => request<void>('/auth/logout', { method: 'POST' }),
}

export const spaceApi = {
  async list() {
    const data = await request<KnowledgeSpace[] | { spaces: KnowledgeSpace[] }>('/spaces')
    return normalizeList<KnowledgeSpace>(data, ['spaces', 'items'])
  },
}

export const chatApi = {
  async sessions() {
    const data = await request<ChatSession[] | { sessions: ChatSession[] }>('/chat/sessions')
    return normalizeList<ChatSession>(data, ['sessions', 'items'])
  },
  async messages(sessionId: number) {
    const data = await request<StoredExchange[] | { messages: StoredExchange[] }>(`/chat/sessions/${sessionId}/messages`)
    return normalizeList<StoredExchange>(data, ['messages', 'items'])
  },
  ask(query: string, sessionId?: number) {
    return request<ChatReply>('/chat/query', {
      method: 'POST',
      body: JSON.stringify({ query, session_id: sessionId || null }),
    })
  },
  askStream(query: string, sessionId?: number) {
    return streamRequest('/chat/query/stream', {
      method: 'POST',
      body: JSON.stringify({ query, session_id: sessionId || null }),
    })
  },
  feedback(messageId: number, feedbackType: 'thumbs_up' | 'thumbs_down', feedbackContent?: string) {
    return request<{ success: boolean; message?: string }>(`/chat/messages/${messageId}/feedback`, {
      method: 'PUT',
      body: JSON.stringify({ feedback_type: feedbackType, content: feedbackContent }),
    })
  },
}

export const documentApi = {
  async list(params?: { search?: string; space_id?: number; status?: string }) {
    const data = await request<DocumentItem[] | { documents: DocumentItem[] }>(`/documents${queryString(params)}`)
    return normalizeList<DocumentItem>(data, ['documents', 'items'])
  },
  async upload(files: File[], spaceId: number) {
    const results = await Promise.all(files.map((file) => {
      const form = new FormData()
      form.append('file', file)
      form.append('space_id', String(spaceId))
      return request<DocumentTaskSubmission>('/documents/upload', {
        method: 'POST', body: form,
      })
    }))
    return { success: true, message: `已提交 ${results.length} 个文件进行处理`, results }
  },
  remove: (documentId: number) => request<{ success: boolean; message?: string }>(`/documents/${documentId}`, { method: 'DELETE' }),
  reindex: (documentId: number) => request<DocumentTaskSubmission>(`/documents/${documentId}/reindex`, { method: 'POST' }),
  deactivate: (documentId: number) => request<{ success: boolean; message?: string }>(`/documents/${documentId}/deactivate`, { method: 'POST' }),
  restore: (documentId: number) => request<{ success: boolean; message?: string }>(`/documents/${documentId}/restore`, {
    method: 'POST', body: JSON.stringify({}),
  }),
}

export const taskApi = {
  async get(taskId: string, signal?: AbortSignal) {
    const task = await request<Omit<TaskStatus, 'status'> & { status: TaskStatus['status'] | 'running' }>(`/tasks/${taskId}`, { signal })
    return {
      ...task,
      // Older workers used `running`; expose one stable state to the UI.
      status: task.status === 'running' ? 'processing' : task.status,
    } satisfies TaskStatus
  },
}

export const auditApi = {
  async list(params: { page: number; page_size: number; action_type?: string; is_success?: boolean; user_id?: number }) {
    const data = await request<Paginated<AuditLog> | { logs: AuditLog[]; total: number; page?: number; page_size?: number }>(
      `/audit/logs${queryString(params)}`,
    )
    if ('items' in data) return data
    return { items: data.logs || [], total: data.total || 0, page: data.page || params.page, page_size: data.page_size || params.page_size }
  },
}

export { TOKEN_KEY }
