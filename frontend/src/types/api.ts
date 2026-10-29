export type UserRole = 'employee' | 'service' | 'admin'

export interface User {
  id: number
  username: string
  role: UserRole
}

export interface KnowledgeSpace {
  id: number
  name: string
  code: string
  description?: string | null
}

export interface ReferenceSource {
  document_id?: number
  document_version_id?: number
  source_name?: string
  file_name?: string
  page?: number | string | null
  section?: string | null
  chunk_index?: number
  content?: string
  relevance_score?: number
  score?: number
}

export interface ChatSession {
  id: number
  title: string
  message_count: number
  created_at: string
  updated_at: string
}

export interface StoredExchange {
  id: number
  user_query: string
  system_answer: string
  references: ReferenceSource[]
  response_time?: number
  created_at: string
}

export interface ChatReply {
  answer: string
  session_id: number
  message_id: number
  references: ReferenceSource[]
  has_answer?: boolean
  refusal_reason?: string | null
  total_time_ms?: number
  model_name?: string
  metrics?: {
    total_time_ms?: number
    retrieval_time_ms?: number
    generation_time_ms?: number
    [key: string]: number | undefined
  }
}

export type ChatStreamEvent =
  | { type: 'meta'; session_id?: number; message_id?: number }
  | { type: 'delta'; content: string }
  | ({ type: 'done' } & ChatReply)
  | { type: 'error'; message: string; detail?: unknown }

export type DocumentStatus = 'pending' | 'processing' | 'success' | 'failed' | 'inactive' | 'deleted' | 'unknown'

export interface DocumentItem {
  id: number
  name: string
  space_id: number
  space_name: string
  version_number: number
  version_status: DocumentStatus
  file_size: number
  chunk_count: number
  created_by: string
  created_at: string
  indexed_at?: string | null
  is_deleted?: boolean
  error_message?: string | null
}

export type TaskState = 'queued' | 'processing' | 'succeeded' | 'failed'

export interface DocumentTaskSubmission {
  task_id: string
  status: TaskState
  message?: string
  document_id: number
  version_id: number
  is_new_version?: boolean
}

export interface TaskStatus {
  task_id: string
  status: TaskState
  progress?: number
  message?: string | null
  error?: string | null
  document_id?: number
  version_id?: number
  created_at?: string
  updated_at?: string
}

export interface AuditLog {
  id: number
  created_at: string
  user_id: number
  username: string
  user_role: UserRole | string
  action_type: string
  action_target?: string | null
  query_summary?: string | null
  is_success: boolean
  has_answer?: boolean | null
  refusal_reason?: string | null
  model_name?: string | null
  response_time?: number | null
  retrieved_count?: number
  filtered_count?: number
  valid_count?: number
  reference_count?: number
  error_message?: string | null
}

export interface Paginated<T> {
  items: T[]
  total: number
  page: number
  page_size: number
}
