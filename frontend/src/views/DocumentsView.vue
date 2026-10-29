<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import {
  ArchiveRestore, Check, ChevronLeft, ChevronRight, FileSearch, FileText, Filter,
  MoreHorizontal, RefreshCw, Search, Trash2, UploadCloud, X,
} from '@lucide/vue'
import EmptyState from '@/components/EmptyState.vue'
import LoadingBlock from '@/components/LoadingBlock.vue'
import StatusBadge from '@/components/StatusBadge.vue'
import { ApiError, documentApi, spaceApi, taskApi } from '@/services/api'
import type { DocumentItem, KnowledgeSpace, TaskStatus } from '@/types/api'
import { errorMessage, formatBytes, formatDate } from '@/utils/format'

const documents = ref<DocumentItem[]>([])
const spaces = ref<KnowledgeSpace[]>([])
const loading = ref(true)
const actionLoading = ref<number>()
const error = ref('')
const success = ref('')
const search = ref('')
const status = ref('')
const spaceId = ref<number | ''>('')
const selected = ref<Set<number>>(new Set())
const menuOpen = ref<number>()
const uploadOpen = ref(false)
const files = ref<File[]>([])
const uploadSpace = ref<number | ''>('')
const uploading = ref(false)
const dragging = ref(false)
const fileInput = ref<HTMLInputElement>()
const page = ref(1)
const pageSize = 10
const activeTasks = ref<Array<TaskStatus & { label: string }>>([])
const pollingControllers = new Set<AbortController>()
let lastBackgroundRefresh = 0
let backgroundRefreshRunning = false

const filtered = computed(() => documents.value.filter((item) => {
  const nameMatches = !search.value || item.name.toLowerCase().includes(search.value.toLowerCase())
  const statusMatches = !status.value || item.version_status === status.value
  const spaceMatches = !spaceId.value || item.space_id === spaceId.value
  return nameMatches && statusMatches && spaceMatches
}))
const pageCount = computed(() => Math.max(1, Math.ceil(filtered.value.length / pageSize)))
const pageItems = computed(() => filtered.value.slice((page.value - 1) * pageSize, page.value * pageSize))
const stats = computed(() => ({
  total: documents.value.length,
  active: documents.value.filter((item) => item.version_status === 'success').length,
  processing: documents.value.filter((item) => ['pending', 'processing'].includes(item.version_status)).length,
  failed: documents.value.filter((item) => item.version_status === 'failed').length,
}))
const allPageSelected = computed(() => pageItems.value.length > 0 && pageItems.value.every((item) => selected.value.has(item.id)))

async function load(silent = false) {
  if (!silent) {
    loading.value = true
    error.value = ''
  }
  try {
    ;[documents.value, spaces.value] = await Promise.all([documentApi.list(), spaceApi.list()])
    if (!uploadSpace.value && spaces.value.length) uploadSpace.value = spaces.value[0].id
  } catch (reason) {
    error.value = errorMessage(reason)
  } finally {
    if (!silent) loading.value = false
  }
}

function updateActiveTask(task: TaskStatus & { label: string }) {
  const index = activeTasks.value.findIndex((item) => item.task_id === task.task_id)
  if (index === -1) activeTasks.value = [...activeTasks.value, task]
  else activeTasks.value = activeTasks.value.map((item, itemIndex) => itemIndex === index ? task : item)
}

async function refreshDocumentsInBackground() {
  const now = Date.now()
  if (backgroundRefreshRunning || now - lastBackgroundRefresh < 2500) return
  backgroundRefreshRunning = true
  lastBackgroundRefresh = now
  try {
    await load(true)
  } finally {
    backgroundRefreshRunning = false
  }
}

function pollingDelay(milliseconds: number, signal: AbortSignal) {
  return new Promise<void>((resolve, reject) => {
    const timer = window.setTimeout(() => {
      signal.removeEventListener('abort', onAbort)
      resolve()
    }, milliseconds)
    const onAbort = () => {
      window.clearTimeout(timer)
      reject(new DOMException('任务轮询已取消', 'AbortError'))
    }
    signal.addEventListener('abort', onAbort, { once: true })
  })
}

async function waitForTask(taskId: string, label: string, timeoutMs = 180000) {
  const controller = new AbortController()
  pollingControllers.add(controller)
  const deadline = Date.now() + timeoutMs
  try {
    while (Date.now() < deadline) {
      const task = await taskApi.get(taskId, controller.signal)
      updateActiveTask({ ...task, label })
      void refreshDocumentsInBackground()
      if (task.status === 'succeeded') return task
      if (task.status === 'failed') throw new ApiError(task.message || task.error || `${label}处理失败`, 500, task)
      await pollingDelay(1000, controller.signal)
    }
    throw new ApiError(`${label}处理超时，请稍后刷新列表查看状态`, 408)
  } finally {
    pollingControllers.delete(controller)
  }
}

function toggleAll() {
  const next = new Set(selected.value)
  if (allPageSelected.value) pageItems.value.forEach((item) => next.delete(item.id))
  else pageItems.value.forEach((item) => next.add(item.id))
  selected.value = next
}

function toggleOne(id: number) {
  const next = new Set(selected.value)
  next.has(id) ? next.delete(id) : next.add(id)
  selected.value = next
}

function addFiles(newFiles: File[]) {
  const allowed = ['.pdf', '.docx', '.txt', '.xlsx']
  const accepted = newFiles.filter((file) => allowed.some((ext) => file.name.toLowerCase().endsWith(ext)))
  const known = new Set(files.value.map((file) => `${file.name}:${file.size}`))
  files.value = [...files.value, ...accepted.filter((file) => !known.has(`${file.name}:${file.size}`))]
}

function onFileChange(event: Event) {
  const target = event.target as HTMLInputElement
  addFiles(Array.from(target.files || []))
  target.value = ''
}

function onDrop(event: DragEvent) {
  dragging.value = false
  addFiles(Array.from(event.dataTransfer?.files || []))
}

async function upload() {
  if (!files.value.length || !uploadSpace.value) return
  uploading.value = true
  error.value = ''
  success.value = ''
  const submittedFiles = [...files.value]
  let submittedTaskIds: string[] = []
  try {
    const result = await documentApi.upload(submittedFiles, uploadSpace.value)
    submittedTaskIds = result.results.map((task) => task.task_id)
    uploadOpen.value = false
    files.value = []
    result.results.forEach((task, index) => updateActiveTask({ ...task, label: submittedFiles[index]?.name || `文档 ${index + 1}` }))
    await load()
    const outcomes = await Promise.allSettled(result.results.map((task, index) => (
      waitForTask(task.task_id, submittedFiles[index]?.name || `文档 ${index + 1}`)
    )))
    const failed = outcomes.filter((outcome) => outcome.status === 'rejected')
    if (failed.length) {
      const first = failed[0] as PromiseRejectedResult
      error.value = `${failed.length}/${outcomes.length} 个文档处理失败：${errorMessage(first.reason)}`
    } else {
      success.value = `${outcomes.length} 个文档已完成索引`
    }
    await load()
  } catch (reason) {
    error.value = errorMessage(reason)
  } finally {
    activeTasks.value = activeTasks.value.filter((task) => !submittedTaskIds.includes(task.task_id))
    uploading.value = false
  }
}

async function runAction(document: DocumentItem, action: 'delete' | 'reindex' | 'deactivate' | 'restore') {
  menuOpen.value = undefined
  const labels = { delete: '删除', reindex: '重新索引', deactivate: '下架', restore: '恢复' }
  if ((action === 'delete' || action === 'deactivate') && !window.confirm(`确认${labels[action]}“${document.name}”？`)) return
  actionLoading.value = document.id
  error.value = ''
  success.value = ''
  let actionTaskId: string | undefined
  try {
    const result = action === 'delete' ? await documentApi.remove(document.id)
      : action === 'reindex' ? await documentApi.reindex(document.id)
        : action === 'deactivate' ? await documentApi.deactivate(document.id)
          : await documentApi.restore(document.id)
    if (action === 'reindex' && 'task_id' in result) {
      actionTaskId = result.task_id
      updateActiveTask({ ...result, label: document.name })
      await load()
      await waitForTask(result.task_id, document.name)
      success.value = `“${document.name}”已完成重新索引`
    } else {
      success.value = result.message || `${labels[action]}操作已完成`
    }
    await load()
  } catch (reason) {
    error.value = errorMessage(reason)
  } finally {
    if (actionTaskId) activeTasks.value = activeTasks.value.filter((task) => task.task_id !== actionTaskId)
    actionLoading.value = undefined
  }
}

async function bulkDelete() {
  if (!selected.value.size || !window.confirm(`确认删除选中的 ${selected.value.size} 份文档？`)) return
  error.value = ''
  const ids = [...selected.value]
  const results = await Promise.allSettled(ids.map((id) => documentApi.remove(id)))
  const succeeded = results.filter((result) => result.status === 'fulfilled').length
  selected.value = new Set()
  success.value = `批量操作完成：${succeeded} 份成功，${ids.length - succeeded} 份失败`
  await load()
}

onMounted(load)
onBeforeUnmount(() => pollingControllers.forEach((controller) => controller.abort()))
</script>

<template>
  <div class="page documents-page" @click.self="menuOpen = undefined">
    <header class="page-header">
      <div><p class="eyebrow">知识库治理</p><h1>文档管理</h1><p>维护文档版本、索引状态与知识空间归属。</p></div>
      <button class="button primary" :disabled="uploading" @click="uploadOpen = true"><UploadCloud :size="18" />上传文档</button>
    </header>

    <div v-if="error" class="notice error page-notice">{{ error }}<button class="icon-button compact" title="关闭" @click="error = ''"><X :size="15" /></button></div>
    <div v-if="success" class="notice success page-notice"><Check :size="16" />{{ success }}<button class="icon-button compact" title="关闭" @click="success = ''"><X :size="15" /></button></div>
    <div v-if="activeTasks.length" class="notice page-notice" role="status">
      <span class="spinner" />
      正在处理 {{ activeTasks.length }} 个任务：{{ activeTasks.map((task) => `${task.label}${task.progress !== undefined ? ` ${task.progress}%` : ''}`).join('、') }}
    </div>

    <section class="metrics-grid compact-metrics">
      <article class="metric-block"><div><span>全部文档</span><strong>{{ stats.total }}</strong></div></article>
      <article class="metric-block"><div><span>已生效</span><strong>{{ stats.active }}</strong></div></article>
      <article class="metric-block"><div><span>处理中</span><strong>{{ stats.processing }}</strong></div></article>
      <article class="metric-block"><div><span>异常</span><strong>{{ stats.failed }}</strong></div></article>
    </section>

    <section class="section-block table-section">
      <div class="table-toolbar">
        <div class="filter-input"><Search :size="17" /><input v-model="search" placeholder="搜索文档名称" @input="page = 1" /></div>
        <div class="filter-controls">
          <label class="select-wrap"><Filter :size="16" /><select v-model="spaceId" @change="page = 1"><option value="">全部空间</option><option v-for="space in spaces" :key="space.id" :value="space.id">{{ space.name }}</option></select></label>
          <label class="select-wrap"><select v-model="status" @change="page = 1"><option value="">全部状态</option><option value="success">已生效</option><option value="processing">处理中</option><option value="pending">待处理</option><option value="failed">失败</option><option value="inactive">已下架</option></select></label>
          <button class="icon-button" title="刷新列表" @click="load()"><RefreshCw :size="17" /></button>
        </div>
      </div>

      <div v-if="selected.size" class="bulk-bar"><span>已选择 <strong>{{ selected.size }}</strong> 项</span><button class="button danger small" @click="bulkDelete"><Trash2 :size="16" />批量删除</button></div>
      <LoadingBlock v-if="loading" label="正在读取文档" />
      <EmptyState v-else-if="!filtered.length" title="没有匹配的文档" description="调整筛选条件，或上传新的知识文档。">
        <button class="button secondary small" @click="uploadOpen = true">上传文档</button>
      </EmptyState>

      <div v-else class="data-table-wrap">
        <table class="data-table documents-table">
          <thead><tr><th class="check-cell"><input type="checkbox" :checked="allPageSelected" aria-label="选择本页" @change="toggleAll" /></th><th>文档</th><th>知识空间</th><th>版本</th><th>状态</th><th>大小 / 分片</th><th>更新时间</th><th class="action-cell">操作</th></tr></thead>
          <tbody>
            <tr v-for="document in pageItems" :key="document.id">
              <td class="check-cell"><input type="checkbox" :checked="selected.has(document.id)" :aria-label="`选择 ${document.name}`" @change="toggleOne(document.id)" /></td>
              <td><div class="file-cell"><span class="file-icon"><FileText :size="18" /></span><div><strong>{{ document.name }}</strong><small>由 {{ document.created_by }} 上传</small></div></div></td>
              <td>{{ document.space_name }}</td><td>v{{ document.version_number }}</td><td><StatusBadge :status="document.version_status" /></td>
              <td><span class="stacked-cell">{{ formatBytes(document.file_size) }}<small>{{ document.chunk_count }} 个分片</small></span></td>
              <td><span class="stacked-cell">{{ formatDate(document.indexed_at || document.created_at, false) }}<small>{{ document.indexed_at ? '完成索引' : '创建时间' }}</small></span></td>
              <td class="action-cell">
                <button class="icon-button compact" :disabled="actionLoading === document.id" title="更多操作" @click.stop="menuOpen = menuOpen === document.id ? undefined : document.id">
                  <span v-if="actionLoading === document.id" class="spinner" /><MoreHorizontal v-else :size="18" />
                </button>
                <div v-if="menuOpen === document.id" class="action-menu" @click.stop>
                  <button @click="runAction(document, 'reindex')"><RefreshCw :size="15" />重新索引</button>
                  <button v-if="document.version_status === 'success'" @click="runAction(document, 'deactivate')"><FileSearch :size="15" />下架文档</button>
                  <button v-if="document.version_status === 'inactive'" @click="runAction(document, 'restore')"><ArchiveRestore :size="15" />恢复文档</button>
                  <button class="danger-text" @click="runAction(document, 'delete')"><Trash2 :size="15" />删除文档</button>
                </div>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
      <div v-if="filtered.length" class="pagination"><span>共 {{ filtered.length }} 项</span><div><button class="icon-button compact" title="上一页" :disabled="page <= 1" @click="page--"><ChevronLeft :size="17" /></button><span>{{ page }} / {{ pageCount }}</span><button class="icon-button compact" title="下一页" :disabled="page >= pageCount" @click="page++"><ChevronRight :size="17" /></button></div></div>
    </section>

    <div v-if="uploadOpen" class="modal-backdrop" @click.self="uploadOpen = false">
      <section class="modal upload-modal" role="dialog" aria-modal="true" aria-labelledby="upload-title">
        <header><div><h2 id="upload-title">上传知识文档</h2><p>文件将解析、分片并写入所选知识空间。</p></div><button class="icon-button" title="关闭" @click="uploadOpen = false"><X :size="20" /></button></header>
        <label class="field"><span>知识空间</span><span class="select-wrap full"><select v-model="uploadSpace"><option disabled value="">请选择知识空间</option><option v-for="space in spaces" :key="space.id" :value="space.id">{{ space.name }}</option></select></span></label>
        <button class="drop-zone" :class="{ dragging }" type="button" @click="fileInput?.click()" @dragover.prevent="dragging = true" @dragleave.prevent="dragging = false" @drop.prevent="onDrop">
          <UploadCloud :size="27" /><strong>拖放文件到这里，或点击选择</strong><span>支持 PDF、DOCX、TXT、XLSX，单文件不超过 20 MB</span>
        </button>
        <input ref="fileInput" class="sr-only" type="file" accept=".pdf,.docx,.txt,.xlsx" multiple @change="onFileChange" />
        <div v-if="files.length" class="upload-files">
          <div v-for="(file, index) in files" :key="`${file.name}-${file.size}`"><FileText :size="17" /><span><strong>{{ file.name }}</strong><small>{{ formatBytes(file.size) }}</small></span><button class="icon-button compact" title="移除文件" @click="files.splice(index, 1)"><X :size="16" /></button></div>
        </div>
        <footer><button class="button secondary" :disabled="uploading" @click="uploadOpen = false">取消</button><button class="button primary" :disabled="uploading || !files.length || !uploadSpace" @click="upload"><span v-if="uploading" class="spinner light" /><UploadCloud v-else :size="17" />上传 {{ files.length || '' }}</button></footer>
      </section>
    </div>
  </div>
</template>
