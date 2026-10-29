<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { RouterLink } from 'vue-router'
import { ArrowRight, BookOpenCheck, Clock3, FileCheck2, Files, MessageSquareText, ShieldCheck } from '@lucide/vue'
import EmptyState from '@/components/EmptyState.vue'
import LoadingBlock from '@/components/LoadingBlock.vue'
import StatusBadge from '@/components/StatusBadge.vue'
import { chatApi, documentApi, spaceApi } from '@/services/api'
import { useAuthStore } from '@/stores/auth'
import type { ChatSession, DocumentItem, KnowledgeSpace } from '@/types/api'
import { errorMessage, formatDate, roleNames } from '@/utils/format'

const auth = useAuthStore()
const loading = ref(true)
const error = ref('')
const spaces = ref<KnowledgeSpace[]>([])
const sessions = ref<ChatSession[]>([])
const documents = ref<DocumentItem[]>([])
const activeDocuments = computed(() => documents.value.filter((item) => item.version_status === 'success').length)

async function load() {
  loading.value = true
  error.value = ''
  try {
    const requests: [Promise<KnowledgeSpace[]>, Promise<ChatSession[]>, Promise<DocumentItem[]>] = [
      spaceApi.list(), chatApi.sessions(), auth.isAdmin ? documentApi.list() : Promise.resolve([]),
    ]
    ;[spaces.value, sessions.value, documents.value] = await Promise.all(requests)
  } catch (reason) {
    error.value = errorMessage(reason)
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>

<template>
  <div class="page dashboard-page">
    <header class="page-header dashboard-header">
      <div>
        <p class="eyebrow">{{ roleNames[auth.user?.role || ''] }}工作区</p>
        <h1>你好，{{ auth.user?.username }}</h1>
        <p>从可信知识中查找答案，继续最近的工作。</p>
      </div>
      <RouterLink to="/chat" class="button primary"><MessageSquareText :size="18" />开始提问</RouterLink>
    </header>

    <div v-if="error" class="notice error page-notice">{{ error }} <button class="text-button" @click="load">重试</button></div>
    <LoadingBlock v-if="loading" label="正在汇总工作台数据" />

    <template v-else>
      <section class="metrics-grid" aria-label="数据概览">
        <article class="metric-block">
          <span class="metric-icon green"><BookOpenCheck :size="20" /></span>
          <div><span>可访问知识空间</span><strong>{{ spaces.length }}</strong><small>按当前角色授权</small></div>
        </article>
        <article class="metric-block">
          <span class="metric-icon blue"><MessageSquareText :size="20" /></span>
          <div><span>历史会话</span><strong>{{ sessions.length }}</strong><small>最近更新 {{ sessions[0] ? formatDate(sessions[0].updated_at, false) : '暂无' }}</small></div>
        </article>
        <article v-if="auth.isAdmin" class="metric-block">
          <span class="metric-icon amber"><Files :size="20" /></span>
          <div><span>文档总数</span><strong>{{ documents.length }}</strong><small>{{ activeDocuments }} 份已生效</small></div>
        </article>
        <article class="metric-block">
          <span class="metric-icon neutral"><ShieldCheck :size="20" /></span>
          <div><span>当前权限</span><strong class="role-metric">{{ roleNames[auth.user?.role || ''] }}</strong><small>服务端强制校验</small></div>
        </article>
      </section>

      <div class="dashboard-grid">
        <section class="section-block recent-block">
          <div class="section-heading"><div><h2>最近会话</h2><p>继续上次没有完成的查询</p></div><RouterLink to="/chat" class="text-link">查看全部<ArrowRight :size="15" /></RouterLink></div>
          <div v-if="sessions.length" class="activity-list">
            <RouterLink v-for="session in sessions.slice(0, 5)" :key="session.id" :to="`/chat?session=${session.id}`" class="activity-row">
              <span class="activity-icon"><MessageSquareText :size="18" /></span>
              <span class="activity-copy"><strong>{{ session.title }}</strong><small>{{ session.message_count }} 轮问答 · {{ formatDate(session.updated_at) }}</small></span>
              <ArrowRight :size="17" />
            </RouterLink>
          </div>
          <EmptyState v-else title="还没有历史会话" description="提出第一个问题后，会话会出现在这里。">
            <RouterLink to="/chat" class="button secondary small">开始提问</RouterLink>
          </EmptyState>
        </section>

        <section class="section-block spaces-block">
          <div class="section-heading"><div><h2>知识空间</h2><p>你当前可以检索的内容范围</p></div></div>
          <div v-if="spaces.length" class="space-list">
            <article v-for="space in spaces" :key="space.id" class="space-row">
              <span class="space-code">{{ space.code.slice(0, 2).toUpperCase() }}</span>
              <div><strong>{{ space.name }}</strong><p>{{ space.description || '已授权访问该知识空间' }}</p></div>
              <StatusBadge status="success" label="可访问" />
            </article>
          </div>
          <EmptyState v-else title="暂无可访问空间" description="请联系管理员检查你的角色权限。" />
        </section>
      </div>

      <section v-if="auth.isAdmin" class="section-block document-snapshot">
        <div class="section-heading"><div><h2>文档状态</h2><p>最近接入知识库的文档</p></div><RouterLink to="/documents" class="text-link">管理文档<ArrowRight :size="15" /></RouterLink></div>
        <div v-if="documents.length" class="compact-table">
          <div class="compact-row compact-head"><span>文档</span><span>知识空间</span><span>状态</span><span>更新时间</span></div>
          <div v-for="document in documents.slice(0, 5)" :key="document.id" class="compact-row">
            <span class="document-name"><FileCheck2 :size="17" />{{ document.name }}</span><span>{{ document.space_name }}</span>
            <span><StatusBadge :status="document.version_status" /></span><span><Clock3 :size="14" />{{ formatDate(document.indexed_at || document.created_at) }}</span>
          </div>
        </div>
        <EmptyState v-else title="尚未上传文档" description="上传文档并完成索引后，团队即可检索。" />
      </section>
    </template>
  </div>
</template>
