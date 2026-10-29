<script setup lang="ts">
import { computed } from 'vue'

const props = defineProps<{ status: string; label?: string }>()
const text: Record<string, string> = {
  success: '已生效', pending: '待处理', processing: '处理中', failed: '失败',
  inactive: '已下架', deleted: '已删除', unknown: '未知', true: '成功', false: '失败',
}
const normalized = computed(() => String(props.status).toLowerCase())
</script>

<template>
  <span class="status-badge" :class="`status-${normalized}`">
    <span class="status-dot" />{{ label || text[normalized] || status }}
  </span>
</template>
