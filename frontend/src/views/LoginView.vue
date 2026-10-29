<script setup lang="ts">
import { computed, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ArrowRight, BookOpen, CheckCircle2, Eye, EyeOff, LockKeyhole, UserRound } from '@lucide/vue'
import { useAuthStore } from '@/stores/auth'
import { errorMessage } from '@/utils/format'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()
const username = ref('')
const password = ref('')
const showPassword = ref(false)
const error = ref('')
const expired = computed(() => route.query.expired === '1')

async function submit() {
  error.value = ''
  if (!username.value.trim() || !password.value) {
    error.value = '请输入用户名和密码'
    return
  }
  try {
    await auth.login(username.value.trim(), password.value)
    const target = typeof route.query.redirect === 'string' ? route.query.redirect : '/dashboard'
    await router.replace(target)
  } catch (reason) {
    error.value = errorMessage(reason)
  }
}
</script>

<template>
  <main class="login-page">
    <section class="login-intro">
      <div class="login-brand"><span class="brand-mark"><BookOpen :size="21" /></span><strong>KnowBase</strong></div>
      <div class="intro-copy">
        <p class="eyebrow">企业私有知识中心</p>
        <h1>让每一次回答<br>都有据可查</h1>
        <p>基于权限感知检索、混合召回与可信引用，为团队提供稳定的知识问答体验。</p>
        <ul>
          <li><CheckCircle2 :size="17" />答案关联原始文档与具体位置</li>
          <li><CheckCircle2 :size="17" />不同角色只访问授权知识空间</li>
          <li><CheckCircle2 :size="17" />文档版本与操作过程全程可追溯</li>
        </ul>
      </div>
      <p class="login-footnote">KnowBase · Private Knowledge Assistant</p>
    </section>

    <section class="login-panel">
      <form class="login-form" @submit.prevent="submit">
        <div class="login-heading">
          <span class="mobile-login-brand"><BookOpen :size="22" /></span>
          <p class="eyebrow">欢迎回来</p>
          <h2>登录工作台</h2>
          <p>使用管理员分配的账号继续</p>
        </div>
        <div v-if="expired" class="notice warning">登录已过期，请重新验证身份。</div>
        <div v-if="error" class="notice error" role="alert">{{ error }}</div>

        <label class="field">
          <span>用户名</span>
          <span class="input-wrap"><UserRound :size="18" /><input v-model="username" autocomplete="username" autofocus placeholder="请输入用户名" /></span>
        </label>
        <label class="field">
          <span>密码</span>
          <span class="input-wrap">
            <LockKeyhole :size="18" />
            <input v-model="password" :type="showPassword ? 'text' : 'password'" autocomplete="current-password" placeholder="请输入密码" />
            <button class="input-action" type="button" :title="showPassword ? '隐藏密码' : '显示密码'" @click="showPassword = !showPassword">
              <EyeOff v-if="showPassword" :size="18" /><Eye v-else :size="18" />
            </button>
          </span>
        </label>
        <button class="button primary login-submit" :disabled="auth.loading" type="submit">
          <span v-if="auth.loading" class="spinner light" />
          <template v-else>登录<ArrowRight :size="18" /></template>
        </button>
        <p class="security-note"><LockKeyhole :size="14" />账号与访问范围由管理员统一管理</p>
      </form>
    </section>
  </main>
</template>
