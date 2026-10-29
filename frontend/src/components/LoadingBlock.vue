<script setup lang="ts">
withDefaults(defineProps<{ label?: string }>(), { label: '加载中' })
</script>

<template>
  <div class="loading-block"><span class="spinner" />{{ label }}</div>
</template>
