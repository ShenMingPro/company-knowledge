<script setup lang="ts">
import { computed, nextTick, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  BookOpen, ChevronDown, ChevronRight, Clock3, FileText, Menu, MessageSquarePlus,
  PanelLeftClose, PanelLeftOpen, Send, Sparkles, ThumbsDown, ThumbsUp, X,
} from '@lucide/vue'
import EmptyState from '@/components/EmptyState.vue'
import { ApiError, chatApi } from '@/services/api'
import type { ChatSession, ReferenceSource, StoredExchange } from '@/types/api'
import { errorMessage, formatDate } from '@/utils/format'

interface DisplayMessage {
  key: string
  id?: number
  role: 'user' | 'assistant'
  content: string
  references?: ReferenceSource[]
  responseTime?: number
  createdAt?: string
  feedback?: 'thumbs_up' | 'thumbs_down'
  streaming?: boolean
  failed?: boolean
}

const route = useRoute()
const router = useRouter()
const sessions = ref<ChatSession[]>([])
const activeSessionId = ref<number>()
const messages = ref<DisplayMessage[]>([])
const prompt = ref('')
const loadingSessions = ref(true)
const loadingMessages = ref(false)
const sending = ref(false)
const error = ref('')
const sidebarOpen = ref(true)
const mobileSessionsOpen = ref(false)
const openReferences = ref<Set<string>>(new Set())
const messageArea = ref<HTMLElement>()

const activeSession = computed(() => sessions.value.find((session) => session.id === activeSessionId.value))

function exchangesToMessages(exchanges: StoredExchange[]): DisplayMessage[] {
  return exchanges.flatMap((item) => [
    { key: `u-${item.id}`, role: 'user', content: item.user_query, createdAt: item.created_at },
    {
      key: `a-${item.id}`, id: item.id, role: 'assistant', content: item.system_answer,
      references: item.references || [], responseTime: item.response_time, createdAt: item.created_at,
    },
  ]) as DisplayMessage[]
}

async function loadSessions() {
  loadingSessions.value = true
  try {
    sessions.value = await chatApi.sessions()
  } catch (reason) {
    error.value = errorMessage(reason)
  } finally {
    loadingSessions.value = false
  }
}

async function selectSession(id: number) {
  if (sending.value) return
  activeSessionId.value = id
  mobileSessionsOpen.value = false
  loadingMessages.value = true
  error.value = ''
  await router.replace({ path: '/chat', query: { session: String(id) } })
  try {
    messages.value = exchangesToMessages(await chatApi.messages(id))
    await scrollToBottom()
  } catch (reason) {
    error.value = errorMessage(reason)
  } finally {
    loadingMessages.value = false
  }
}

function newChat() {
  if (sending.value) return
  activeSessionId.value = undefined
  messages.value = []
  error.value = ''
  mobileSessionsOpen.value = false
  router.replace('/chat')
}

async function send() {
  const query = prompt.value.trim()
  if (!query || sending.value) return
  error.value = ''
  prompt.value = ''
  messages.value.push({ key: `local-u-${Date.now()}`, role: 'user', content: query })
  messages.value.push({
    key: `stream-${Date.now()}`, role: 'assistant', content: '', streaming: true,
  })
  // Mutate the proxy returned by the reactive array so every chunk repaints immediately.
  const assistantMessage = messages.value[messages.value.length - 1]
  sending.value = true
  await scrollToBottom()

  try {
    let completed = false
    for await (const event of chatApi.askStream(query, activeSessionId.value)) {
      if (event.type === 'delta') {
        assistantMessage.content += event.content
        await scrollToBottom(false)
      } else if (event.type === 'meta') {
        if (event.session_id) activeSessionId.value = event.session_id
        if (event.message_id) assistantMessage.id = event.message_id
      } else if (event.type === 'done') {
        completed = true
        activeSessionId.value = event.session_id
        assistantMessage.id = event.message_id
        assistantMessage.content ||= event.answer
        assistantMessage.references = event.references || []
        assistantMessage.responseTime = event.metrics?.total_time_ms ?? event.total_time_ms
      } else if (event.type === 'error') {
        throw new ApiError(event.message, 0, event.detail)
      }
    }
    if (!completed) throw new ApiError('回答连接意外中断，请重试', 0)
    assistantMessage.streaming = false
    await router.replace({ path: '/chat', query: { session: String(activeSessionId.value) } })
    await loadSessions()
  } catch (reason) {
    assistantMessage.streaming = false
    assistantMessage.failed = true
    assistantMessage.id = undefined
    assistantMessage.references = []
    const message = errorMessage(reason)
    error.value = message
    assistantMessage.content = `抱歉，本次查询未能完成。${message}`
  } finally {
    sending.value = false
    await scrollToBottom()
  }
}

function handleComposerKeydown(event: KeyboardEvent) {
  if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) {
    event.preventDefault()
    send()
  }
}

function toggleReferences(key: string) {
  const next = new Set(openReferences.value)
  next.has(key) ? next.delete(key) : next.add(key)
  openReferences.value = next
}

async function submitFeedback(message: DisplayMessage, type: 'thumbs_up' | 'thumbs_down') {
  if (!message.id || message.feedback) return
  try {
    await chatApi.feedback(message.id, type)
    message.feedback = type
  } catch (reason) {
    error.value = errorMessage(reason)
  }
}

async function scrollToBottom(smooth = true) {
  await nextTick()
  messageArea.value?.scrollTo({ top: messageArea.value.scrollHeight, behavior: smooth ? 'smooth' : 'auto' })
}

function referenceTitle(reference: ReferenceSource) {
  return reference.source_name || reference.file_name || `文档 #${reference.document_id || '未知'}`
}

function referenceMeta(reference: ReferenceSource) {
  const parts = []
  if (reference.page !== undefined && reference.page !== null) parts.push(`第 ${reference.page} 页`)
  if (reference.section) parts.push(reference.section)
  const score = reference.relevance_score ?? reference.score
  if (score !== undefined) parts.push(`相关度 ${(score * (score <= 1 ? 100 : 1)).toFixed(0)}%`)
  return parts.join(' · ')
}

onMounted(async () => {
  await loadSessions()
  const sessionId = Number(route.query.session)
  if (Number.isInteger(sessionId) && sessionId > 0) await selectSession(sessionId)
})
</script>

<template>
  <div class="chat-page">
    <div v-if="mobileSessionsOpen" class="mobile-scrim" @click="mobileSessionsOpen = false" />
    <aside class="chat-sessions" :class="{ collapsed: !sidebarOpen, 'mobile-open': mobileSessionsOpen }">
      <div class="session-toolbar">
        <strong>会话记录</strong>
        <button class="icon-button sidebar-close" title="关闭会话列表" @click="mobileSessionsOpen = false"><X :size="19" /></button>
        <button class="icon-button desktop-session-toggle" title="收起会话列表" @click="sidebarOpen = false"><PanelLeftClose :size="18" /></button>
      </div>
      <button class="button secondary new-chat" :disabled="sending" @click="newChat"><MessageSquarePlus :size="17" />新建会话</button>
      <div class="session-list">
        <p v-if="loadingSessions" class="session-loading"><span class="spinner" />加载中</p>
        <template v-else-if="sessions.length">
          <button
            v-for="session in sessions" :key="session.id" class="session-item"
            :class="{ active: session.id === activeSessionId }" :disabled="sending" @click="selectSession(session.id)"
          >
            <span>{{ session.title }}</span><small>{{ formatDate(session.updated_at, false) }} · {{ session.message_count }} 轮</small>
          </button>
        </template>
        <p v-else class="session-empty">暂无历史会话</p>
      </div>
    </aside>

    <section class="chat-workspace">
      <header class="chat-header">
        <div class="chat-header-main">
          <button v-if="!sidebarOpen" class="icon-button desktop-only" title="展开会话列表" @click="sidebarOpen = true"><PanelLeftOpen :size="19" /></button>
          <button class="icon-button mobile-only" title="打开会话列表" @click="mobileSessionsOpen = true"><Menu :size="20" /></button>
          <div><h1>{{ activeSession?.title || '知识问答' }}</h1><p><span class="online-dot" />已连接企业知识库</p></div>
        </div>
        <span class="model-label"><Sparkles :size="15" />混合检索</span>
      </header>

      <div ref="messageArea" class="message-area">
        <div v-if="error" class="notice error chat-notice">{{ error }}</div>
        <div v-if="loadingMessages" class="message-loading"><span class="spinner" />正在读取会话</div>
        <EmptyState v-else-if="!messages.length" title="从一个具体问题开始" description="我会基于你有权访问的知识空间检索，并附上原始资料。">
          <div class="suggestion-grid">
            <button @click="prompt = '请概括当前知识库包含哪些主题？'">知识库包含哪些主题？</button>
            <button @click="prompt = '请查找与客户服务流程相关的制度。'">查找客户服务流程</button>
            <button @click="prompt = '根据现有资料总结主要注意事项。'">总结主要注意事项</button>
          </div>
        </EmptyState>

        <div v-else class="message-stream">
          <article v-for="message in messages" :key="message.key" class="message" :class="message.role">
            <div v-if="message.role === 'assistant'" class="assistant-mark"><BookOpen :size="17" /></div>
            <div class="message-content">
              <div v-if="message.streaming && !message.content" class="thinking"><span /><span /><span />正在检索并组织答案</div>
              <div v-else class="message-bubble" :class="{ streaming: message.streaming, failed: message.failed }">{{ message.content }}</div>
              <div v-if="message.role === 'assistant' && message.references?.length" class="reference-block">
                <button class="reference-toggle" @click="toggleReferences(message.key)">
                  <ChevronDown v-if="openReferences.has(message.key)" :size="16" /><ChevronRight v-else :size="16" />
                  {{ message.references.length }} 条引用来源
                </button>
                <div v-if="openReferences.has(message.key)" class="reference-list">
                  <article v-for="(reference, index) in message.references" :key="index" class="reference-item">
                    <span class="reference-number">{{ index + 1 }}</span>
                    <div><strong><FileText :size="15" />{{ referenceTitle(reference) }}</strong><small>{{ referenceMeta(reference) || '文档片段' }}</small><p v-if="reference.content">{{ reference.content }}</p></div>
                  </article>
                </div>
              </div>
              <div v-if="message.role === 'assistant'" class="message-meta">
                <span v-if="message.responseTime"><Clock3 :size="13" />{{ (message.responseTime / 1000).toFixed(1) }} 秒</span>
                <span v-if="message.id && !message.streaming" class="feedback-actions">
                  <button :class="{ selected: message.feedback === 'thumbs_up' }" title="回答有帮助" @click="submitFeedback(message, 'thumbs_up')"><ThumbsUp :size="14" /></button>
                  <button :class="{ selected: message.feedback === 'thumbs_down' }" title="回答需改进" @click="submitFeedback(message, 'thumbs_down')"><ThumbsDown :size="14" /></button>
                </span>
              </div>
            </div>
          </article>
        </div>
      </div>

      <footer class="composer-wrap">
        <div class="composer">
          <textarea v-model="prompt" rows="1" placeholder="输入问题，Enter 发送，Shift + Enter 换行" :disabled="sending" @keydown="handleComposerKeydown" />
          <button class="send-button" :disabled="!prompt.trim() || sending" title="发送问题" @click="send"><Send :size="19" /></button>
        </div>
        <p>回答由 AI 生成，请结合引用来源核验重要信息。</p>
      </footer>
    </section>
  </div>
</template>
