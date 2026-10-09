// 认证相关 API 封装（register：T021；login/me：T027；logout：T034）
import client from './client'

export function register(username, password) {
  return client.post('/api/auth/register', { username, password })
}

export function login(username, password) {
  return client.post('/api/auth/login', { username, password })
}

export function fetchMe() {
  return client.get('/api/auth/me')
}

export function logout() {
  return client.post('/api/auth/logout')
}
