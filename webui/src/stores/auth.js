// 认证状态（Pinia）：令牌持久化于 localStorage（口径统一走 token.js），
// 启动时由 App 调 restore() 恢复身份；退出动作 logout 在 T034 追加。
import { defineStore } from 'pinia'
import * as authApi from '../api/auth'
import { clearToken, getToken, setToken } from '../api/token'

export const useAuthStore = defineStore('auth', {
  state: () => ({
    token: getToken(),
    user: null,
    restoring: false,
  }),

  getters: {
    isAuthenticated: (state) => Boolean(state.token),
    username: (state) => state.user?.username || '',
  },

  actions: {
    async login(username, password) {
      const { data } = await authApi.login(username, password)
      setToken(data.access_token)
      this.token = data.access_token
      await this.fetchMe()
    },

    async fetchMe() {
      const { data } = await authApi.fetchMe()
      this.user = data
      return data
    },

    // 启动恢复：有令牌则拉取本人；令牌失效（如过期/已退出）则清空
    async restore() {
      if (!this.token) return
      this.restoring = true
      try {
        await this.fetchMe()
      } catch {
        this.clear()
      } finally {
        this.restoring = false
      }
    },

    // T034：调服务端使旧令牌失效；无论接口结果如何都丢弃本地会话
    async logout() {
      try {
        await authApi.logout()
      } finally {
        this.clear()
      }
    },

    clear() {
      clearToken()
      this.token = ''
      this.user = null
    },
  },
})
