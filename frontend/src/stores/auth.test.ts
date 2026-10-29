import { createPinia, setActivePinia } from 'pinia'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { authApi, TOKEN_KEY } from '@/services/api'
import { useAuthStore } from './auth'

describe('auth store logout', () => {
  afterEach(() => {
    vi.restoreAllMocks()
    vi.unstubAllGlobals()
  })

  it('clears the local session even when token revocation fails', async () => {
    const values = new Map<string, string>([
      [TOKEN_KEY, 'access-token'],
      ['knowbase_user', JSON.stringify({ id: 1, username: 'employee', role: 'employee' })],
    ])
    vi.stubGlobal('localStorage', {
      getItem: vi.fn((key: string) => values.get(key) ?? null),
      setItem: vi.fn((key: string, value: string) => values.set(key, value)),
      removeItem: vi.fn((key: string) => values.delete(key)),
    })
    vi.spyOn(authApi, 'logout').mockRejectedValue(new Error('service unavailable'))
    setActivePinia(createPinia())
    const auth = useAuthStore()

    await expect(auth.logout()).rejects.toThrow('service unavailable')

    expect(auth.isAuthenticated).toBe(false)
    expect(values.has(TOKEN_KEY)).toBe(false)
    expect(values.has('knowbase_user')).toBe(false)
  })
})
