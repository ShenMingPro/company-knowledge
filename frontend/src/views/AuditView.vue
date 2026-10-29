<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { CheckCircle2, ChevronLeft, ChevronRight, Clock3, Filter, RefreshCw, Search, ShieldAlert, ShieldCheck, XCircle } from '@lucide/vue'
import EmptyState from '@/components/EmptyState.vue'
import LoadingBlock from '@/components/LoadingBlock.vue'
import StatusBadge from '@/components/StatusBadge.vue'
import { auditApi } from '@/services/api'
import type { AuditLog } from '@/types/api'
import { errorMessage, formatDate, roleNames } from '@/utils/format'

const logs = ref<AuditLog[]>([])
const total = ref(0)
const loading = ref(true)
const error = ref('')
const page = ref(1)
const pageSize = 15
const actionType = ref('')
const result = ref('')
const userId = ref('')
const search = ref('')

const pageCount = computed(() => Math.max(1, Math.ceil(total.value / pageSize)))
const visibleLogs = computed(() => logs.value.filter((log) => !search.value || [log.username, log.query_summary, log.action_target].some((value) => value?.toLowerCase().includes(search.value.toLowerCase()))))
const summary = computed(() => ({
  success: logs.value.filter((log) => log.is_success).length,
  failed: logs.value.filter((log) => !log.is_success).length,
  queries: logs.value.filter((log) => log.action_type === 'rag_query').length,
}))

const actionLabels: Record<string, string> = {
  rag_query: '知识问答', document_upload: '上传文档', document_new_version: '新增版本',
  document_reindex: '重新索引', document_delete: '删除文档', document_deactivate: '下架文档', document_restore: '恢复文档',
}

async function load(reset = false) {
  if (reset) page.value = 1
  loading.value = true
  error.value = ''
  try {
    const response = await auditApi.list({
      page: page.value, page_size: pageSize, action_type: actionType.value || undefined,
      is_success: result.value ? result.value === 'success' : undefined,
      user_id: userId.value ? Number(userId.value) : undefined,
    })
    logs.value = response.items
    total.value = response.total
  } catch (reason) {
    error.value = errorMessage(reason)
  } finally {
    loading.value = false
  }
}

function clearFilters() {
  actionType.value = ''
  result.value = ''
  userId.value = ''
  search.value = ''
  load(true)
}

function changePage(next: number) {
  page.value = next
  load()
}

onMounted(() => load())
</script>

<template>
  <div class="page audit-page">
    <header class="page-header">
      <div><p class="eyebrow">安全与合规</p><h1>审计日志</h1><p>查看知识问答与文档操作的执行记录。</p></div>
      <button class="button secondary" @click="load()"><RefreshCw :size="17" />刷新</button>
    </header>
    <div v-if="error" class="notice error page-notice">{{ error }}<button class="text-button" @click="load()">重试</button></div>

    <section class="metrics-grid compact-metrics audit-metrics">
      <article class="metric-block"><span class="metric-icon neutral"><ShieldCheck :size="19" /></span><div><span>记录总数</span><strong>{{ total }}</strong></div></article>
      <article class="metric-block"><span class="metric-icon green"><CheckCircle2 :size="19" /></span><div><span>本页成功</span><strong>{{ summary.success }}</strong></div></article>
      <article class="metric-block"><span class="metric-icon red"><XCircle :size="19" /></span><div><span>本页失败</span><strong>{{ summary.failed }}</strong></div></article>
      <article class="metric-block"><span class="metric-icon blue"><Search :size="19" /></span><div><span>本页问答</span><strong>{{ summary.queries }}</strong></div></article>
    </section>

    <section class="section-block table-section">
      <div class="audit-filters">
        <div class="filter-input"><Search :size="17" /><input v-model="search" placeholder="搜索当前页内容" /></div>
        <label class="select-wrap"><Filter :size="16" /><select v-model="actionType" @change="load(true)"><option value="">全部操作</option><option value="rag_query">知识问答</option><option value="document_upload">上传文档</option><option value="document_reindex">重新索引</option><option value="document_delete">删除文档</option><option value="document_deactivate">下架文档</option><option value="document_restore">恢复文档</option></select></label>
        <label class="select-wrap"><select v-model="result" @change="load(true)"><option value="">全部结果</option><option value="success">成功</option><option value="failed">失败</option></select></label>
        <div class="filter-input short"><input v-model="userId" inputmode="numeric" placeholder="用户 ID" @keyup.enter="load(true)" /></div>
        <button v-if="actionType || result || userId || search" class="text-button" @click="clearFilters">清除筛选</button>
      </div>

      <LoadingBlock v-if="loading" label="正在读取审计记录" />
      <EmptyState v-else-if="!visibleLogs.length" title="暂无审计记录" description="当前筛选条件下没有匹配结果。" />
      <div v-else class="audit-list">
        <article v-for="log in visibleLogs" :key="log.id" class="audit-row">
          <div class="audit-timeline"><span :class="{ failed: !log.is_success }"><ShieldCheck v-if="log.is_success" :size="16" /><ShieldAlert v-else :size="16" /></span></div>
          <div class="audit-main">
            <div class="audit-title"><strong>{{ actionLabels[log.action_type] || log.action_type }}</strong><StatusBadge :status="String(log.is_success)" /><span>#{{ log.id }}</span></div>
            <p v-if="log.query_summary" class="audit-summary">{{ log.query_summary }}</p>
            <p v-else-if="log.action_target" class="audit-summary muted">操作对象：{{ log.action_target }}</p>
            <div class="audit-meta">
              <span>{{ log.username }} · {{ roleNames[log.user_role] || log.user_role }}</span>
              <span><Clock3 :size="13" />{{ formatDate(log.created_at) }}</span>
              <span v-if="log.response_time">{{ log.response_time }} ms</span>
              <span v-if="log.model_name">{{ log.model_name }}</span>
              <span v-if="log.reference_count !== undefined">{{ log.reference_count }} 条引用</span>
            </div>
            <p v-if="log.error_message" class="audit-error">{{ log.error_message }}</p>
          </div>
        </article>
      </div>

      <div v-if="total" class="pagination"><span>共 {{ total }} 条记录</span><div><button class="icon-button compact" title="上一页" :disabled="page <= 1" @click="changePage(page - 1)"><ChevronLeft :size="17" /></button><span>{{ page }} / {{ pageCount }}</span><button class="icon-button compact" title="下一页" :disabled="page >= pageCount" @click="changePage(page + 1)"><ChevronRight :size="17" /></button></div></div>
    </section>
  </div>
</template>
