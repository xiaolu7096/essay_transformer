import { createApp } from 'vue'
import { createPinia } from 'pinia'
import App from './App.vue'
import router from './router'
import { useAuthStore } from './stores/auth'
import { setUnauthorizedHandler } from './api/client'
import './styles/tokens.css'
import './styles/main.css'

// Naive UI 采用组件内按需 import（无需 app.use）；全局 Provider 在 T036 接入
const app = createApp(App)
app.use(createPinia())
app.use(router)

// T033：任何受保护请求收到 401（过期/伪造/已退出）→ 清空会话并携路径回登录页。
// 登录接口自身的 401 是“凭据错误”，不在此处理（由登录页展示文案）。
setUnauthorizedHandler((error) => {
  const url = error?.config?.url || ''
  if (url.includes('/api/auth/login')) return
  const auth = useAuthStore()
  auth.clear()
  const current = router.currentRoute.value
  if (current.name !== 'login') {
    router.push({ name: 'login', query: { redirect: current.fullPath } })
  }
})

// T027：挂载前恢复本地会话（令牌存在则拉取本人身份），再放行首屏导航
const auth = useAuthStore()
auth.restore().finally(() => app.mount('#app'))
