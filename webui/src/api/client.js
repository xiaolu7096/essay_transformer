// 唯一 axios 实例（FR-017）：业务页面一律使用此实例，不自行附带凭证。
// 基座版（T010）：请求注入令牌 + 统一网络不可用通道；
// 401 清空会话/跳登录由 T033 守卫阶段回填（依赖 auth store）。
import axios from 'axios'
import { getToken } from './token'

export const networkUnavailableListeners = new Set()

export function onNetworkUnavailable(fn) {
  networkUnavailableListeners.add(fn)
  return () => networkUnavailableListeners.delete(fn)
}

function notifyNetworkUnavailable() {
  networkUnavailableListeners.forEach((fn) => fn())
}

const client = axios.create({
  baseURL: '/',
  timeout: 30000,
})

client.interceptors.request.use((config) => {
  const token = getToken()
  if (token) {
    config.headers.Authorization = `Bearer ${token}`
  }
  return config
})

client.interceptors.response.use(
  (response) => response,
  (error) => {
    // 无 response = 网络层失败/后端不可达
    if (!error.response) {
      notifyNetworkUnavailable()
    }
    // 401 处理在 T033 通过下方注册函数回填
    if (error.response?.status === 401 && unauthorizedHandler) {
      unauthorizedHandler(error)
    }
    return Promise.reject(error)
  },
)

// T033 注入：收到 401 时清空会话并携当前路径跳登录
let unauthorizedHandler = null
export function setUnauthorizedHandler(fn) {
  unauthorizedHandler = fn
}

// Spec 003：fetch SSE 通道不经 axios，收到 401 时复用同一全局处理
export function dispatchUnauthorized(url = '') {
  if (unauthorizedHandler) unauthorizedHandler({ config: { url } })
}

export default client
