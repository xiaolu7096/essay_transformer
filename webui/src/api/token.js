// 登录令牌的本地存储唯一口径：client.js 与 auth store 共用，避免 key 漂移
export const TOKEN_KEY = 'et_access_token'

export function getToken() {
  return localStorage.getItem(TOKEN_KEY) || ''
}

export function setToken(token) {
  localStorage.setItem(TOKEN_KEY, token)
}

export function clearToken() {
  localStorage.removeItem(TOKEN_KEY)
}
