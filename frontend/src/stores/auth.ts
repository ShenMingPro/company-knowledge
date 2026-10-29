import { computed, ref } from 'vue'
import { defineStore } from 'pinia'
import { authApi, TOKEN_KEY } from '@/services/api'
import type { User } from '@/types/api'

const USER_KEY = 'knowbase_user'

function cachedUser(): User | null {
  try {
    const value = localStorage.getItem(USER_KEY)
    return value ? JSON.parse(value) as User : null
  } catch {
    localStorage.removeItem(USER_KEY)
    return null
  }
}

export const useAuthStore = defineStore('auth', () => {
  const token = ref(localStorage.getItem(TOKEN_KEY))
  const user = ref<User | null>(cachedUser())
  const loading = ref(false)
  const isAuthenticated = computed(() => Boolean(token.value && user.value))
  const isAdmin = computed(() => user.value?.role === 'admin')

  async function login(username: string, password: string) {
    loading.value = true
    try {
      const result = await authApi.login(username, password)
      if (!result.token) throw new Error('登录响应缺少访问令牌')
      token.value = result.token
      user.value = result.user
      localStorage.setItem(TOKEN_KEY, result.token)
      localStorage.setItem(USER_KEY, JSON.stringify(result.user))
    } finally {
      loading.value = false
    }
  }

  function clearLocalSession() {
    token.value = null
    user.value = null
    localStorage.removeItem(TOKEN_KEY)
    localStorage.removeItem(USER_KEY)
  }

  async function validate() {
    if (!token.value) return false
    try {
      user.value = await authApi.me()
      localStorage.setItem(USER_KEY, JSON.stringify(user.value))
      return true
    } catch {
      clearLocalSession()
      return false
    }
  }

  async function logout(revoke = true) {
    try {
      if (revoke && token.value) await authApi.logout()
    } finally {
      clearLocalSession()
    }
  }

  return { token, user, loading, isAuthenticated, isAdmin, login, logout, clearLocalSession, validate }
})
