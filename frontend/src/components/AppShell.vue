<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { RouterLink, RouterView, useRoute, useRouter } from 'vue-router'
import {
  BookOpen, FileText, LayoutDashboard, LogOut, Menu, MessageSquareText,
  Search, ShieldCheck, X,
} from '@lucide/vue'
import { useAuthStore } from '@/stores/auth'
import { errorMessage } from '@/utils/format'
import { roleNames } from '@/utils/format'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()
const mobileOpen = ref(false)
const logoutError = ref('')

const items = computed(() => [
  { to: '/dashboard', label: '工作台', icon: LayoutDashboard, show: true },
  { to: '/chat', label: '知识问答', icon: MessageSquareText, show: true },
  { to: '/documents', label: '文档管理', icon: FileText, show: auth.isAdmin },
  { to: '/audit', label: '审计日志', icon: ShieldCheck, show: auth.isAdmin },
].filter((item) => item.show))

const pageTitle = computed(() => items.value.find((item) => route.path.startsWith(item.to))?.label || 'KnowBase')

async function logout() {
  logoutError.value = ''
  try {
    await auth.logout()
  } catch (reason) {
    logoutError.value = errorMessage(reason)
  } finally {
    await router.replace('/login')
  }
}

function closeMenu() {
  mobileOpen.value = false
}

function handleExpired() {
  auth.clearLocalSession()
  router.replace({ name: 'login', query: { expired: '1' } })
}

onMounted(() => window.addEventListener('auth:expired', handleExpired))
onBeforeUnmount(() => window.removeEventListener('auth:expired', handleExpired))
</script>

<template>
  <div class="app-frame">
    <div v-if="mobileOpen" class="mobile-scrim" @click="closeMenu" />
    <aside class="sidebar" :class="{ open: mobileOpen }">
      <div class="brand">
        <span class="brand-mark"><BookOpen :size="20" /></span>
        <span><strong>KnowBase</strong><small>企业知识助手</small></span>
        <button class="icon-button sidebar-close" title="关闭导航" @click="closeMenu"><X :size="20" /></button>
      </div>

      <nav class="main-nav" aria-label="主导航">
        <RouterLink
          v-for="item in items" :key="item.to" :to="item.to" class="nav-item"
          :class="{ active: route.path.startsWith(item.to) }" @click="closeMenu"
        >
          <component :is="item.icon" :size="19" />
          <span>{{ item.label }}</span>
        </RouterLink>
      </nav>

      <div class="sidebar-spacer" />
      <div v-if="logoutError" class="sidebar-search-hint" role="alert">{{ logoutError }}</div>
      <div class="sidebar-search-hint"><Search :size="15" /><span>在问答中检索知识</span></div>
      <div class="account-block">
        <div class="avatar">{{ auth.user?.username.slice(0, 1).toUpperCase() }}</div>
        <div class="account-copy">
          <strong>{{ auth.user?.username }}</strong>
          <span>{{ roleNames[auth.user?.role || ''] }}</span>
        </div>
        <button class="icon-button dark" title="退出登录" @click="logout"><LogOut :size="18" /></button>
      </div>
    </aside>

    <main class="app-main">
      <header class="mobile-header">
        <button class="icon-button" title="打开导航" @click="mobileOpen = true"><Menu :size="21" /></button>
        <span>{{ pageTitle }}</span>
        <span class="mobile-logo"><BookOpen :size="19" /></span>
      </header>
      <RouterView />
    </main>
  </div>
</template>
